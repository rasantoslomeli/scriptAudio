"""Owned offline llama.cpp server; authenticated loopback only, proxy bypassed."""
from __future__ import annotations
import json
import re
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import time
import requests
from threading import Event, Thread, Lock
from .edge import EdgeConfig
from .providers import Cancelled, ProviderError


class ContextLimitError(ProviderError):
    pass


class LocalLLM:
    def __init__(self, config_path, cancel, progress):
        self.config = EdgeConfig.load(config_path).verify(summary=True,cancel=cancel)
        self.cancel,self.progress = cancel,progress
        self.process,self.log,self.directory = None,None,None
        self.monitor_done,self.stop_lock,self.monitor = Event(),Lock(),None
        self.session = requests.Session()
        self.session.trust_env = False  # Never send local transcript requests through an HTTP proxy.

    def __enter__(self):
        self.directory = tempfile.TemporaryDirectory(prefix='pss-local-llm-')
        root = Path(self.directory.name)
        key = secrets.token_urlsafe(32)
        key_path = root/'key.txt'
        key_path.write_text(key)
        key_path.chmod(0o600)
        self.headers = {'Authorization':'Bearer '+key}
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0))
            port = sock.getsockname()[1]
        self.base = f'http://127.0.0.1:{port}'
        self.log = (root/'server.log').open('wb')
        config = self.config
        command = [config.summary_executable,'-m',config.summary_model,'--host','127.0.0.1','--port',str(port),
                   '-c',str(config.context_size),'-t',str(config.threads),'--parallel','1','--offline','--no-webui',
                   '-ngl','0' if config.profile=='cpu' else str(config.gpu_layers),'--api-key-file',str(key_path)]
        try:
            self.process = subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=self.log,stderr=self.log,
                creationflags=subprocess.CREATE_NO_WINDOW if __import__('sys').platform=='win32' else 0)
            def watch_cancel():
                while not self.monitor_done.wait(.2):
                    if self.cancel.is_set():
                        self.stop()
                        return
            self.monitor = Thread(target=watch_cancel,daemon=True)
            self.monitor.start()
            deadline = time.monotonic()+240
            while time.monotonic()<deadline:
                self.check()
                if self.process.poll() is not None:
                    raise ProviderError('Local summary engine could not load its model. Check architecture, RAM/VRAM and engine version.')
                try:
                    # Authenticated route confirms we own this server, even if a port race occurred.
                    response = self.session.get(self.base+'/v1/models',headers=self.headers,timeout=(1,2))
                    if response.status_code==200:
                        if config.profile != 'cpu':
                            self.log.flush()
                            text=(root/'server.log').read_text(errors='replace').lower()
                            backend='cuda' if config.profile=='rtx3080' else 'metal'
                            if backend not in text or not re.search(r'offloaded [1-9][0-9]*/[0-9]+ layers to gpu',text):
                                raise ProviderError('Local summary GPU offload was not activated; use a matching native engine or explicitly select a CPU bundle')
                        return self
                except requests.RequestException:
                    pass
                self.progress('Loading offline summary model; recognition GPU process has already exited…')
                if self.cancel.wait(.5):
                    self.check()
            raise ProviderError('Local summary engine startup timed out')
        except BaseException:
            self.__exit__(None,None,None)
            raise

    def check(self):
        if self.cancel.is_set():
            self.stop()
            raise Cancelled('Local summary cancelled')

    def request(self,path,payload):
        self.check()
        try:
            response = self.session.post(self.base+path,headers=self.headers,json=payload,timeout=(3,600))
            self.check()
            response.raise_for_status()
            return response.json()
        except (requests.RequestException,ValueError):
            self.check()
            raise ProviderError('Local summary request failed; no cloud fallback was used') from None

    def complete(self,messages):
        template = self.request('/apply-template',{'messages':messages})['prompt']
        tokenized = self.request('/tokenize',{'content':template,'add_special':True})['tokens']
        if len(tokenized)+self.config.max_summary_tokens+64 > self.config.context_size:
            raise ContextLimitError('Local model context is too small for this source chunk')
        records = json.loads(messages[-1]['content'])
        known = list(dict.fromkeys(record['id'] for record in records))
        schema = {'type':'object','required':['claims'],'additionalProperties':False,'properties':{'claims':{
            'type':'array','items':{'type':'object','required':['text','segment_ids','ambiguous'],'additionalProperties':False,
            'properties':{'text':{'type':'string'},'segment_ids':{'type':'array','minItems':1,'items':{'type':'string','enum':known}},
                          'ambiguous':{'type':'boolean'}}}}}}
        result = self.request('/v1/chat/completions',{'model':'local','messages':messages,'temperature':0,
            'max_tokens':self.config.max_summary_tokens,'response_format':{'type':'json_object','schema':schema}})
        if result.get('choices',[{}])[0].get('finish_reason')=='length':
            raise ProviderError('Local summary reached its output limit; no incomplete claims were accepted. Increase max_summary_tokens or use a smaller scope.')
        return result

    def stop(self):
        with self.stop_lock:
            if self.process and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait()

    def __exit__(self,*args):
        self.monitor_done.set()
        if self.monitor:
            self.monitor.join(timeout=6)
        self.stop()
        self.session.close()
        if self.log:
            self.log.close()
        if self.directory:
            self.directory.cleanup()


def local_summaries(project,mode,speaker_ids,topic,config_path,cancel,progress):
    """Run inference off the GUI thread; release summary GPU allocations on return."""
    from .summaries import summarize
    with LocalLLM(config_path,cancel,progress) as engine:
        return summarize(project,mode,speaker_ids,topic,'','',cancel,progress,
                         completion=engine.complete,provider_name='Offline llama.cpp',chunk_limit=6000)
