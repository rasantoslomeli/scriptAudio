import json
from threading import Event
from types import SimpleNamespace
import pytest
from podcast_studio.local_llm import LocalLLM, ContextLimitError
from podcast_studio.summaries import summarize


def test_local_completion_checks_real_tokens_and_grammar_ids():
    engine=LocalLLM.__new__(LocalLLM)
    engine.config=SimpleNamespace(context_size=8192,max_summary_tokens=1536)
    calls=[]
    def request(path,payload):
        calls.append((path,payload))
        if path=='/apply-template':return {'prompt':'rendered template'}
        if path=='/tokenize':return {'tokens':[1,2,3]}
        return {'choices':[{'finish_reason':'stop','message':{'content':'{"claims": []}'}}]}
    engine.request=request
    messages=[{'role':'user','content':json.dumps([{'id':'s1','text':'Evidence'}])}]
    engine.complete(messages)
    schema=calls[-1][1]['response_format']['schema']
    assert schema['properties']['claims']['items']['properties']['segment_ids']['items']['enum']==['s1']
    assert calls[-1][1]['temperature']==0
    engine.config.context_size=1000
    with pytest.raises(ContextLimitError):engine.complete(messages)


def test_local_context_splitting_does_not_drop_text(project):
    project.segments=[project.segments[0]]
    project.segments[0].text='Long local evidence. '*100
    seen=[]
    def completion(messages):
        data=json.loads(messages[-1]['content'])
        if len(data[0]['text'])>200:
            raise ContextLimitError()
        seen.extend(s['text'] for s in data)
        return {'choices':[{'message':{'content':json.dumps({'claims':[{'text':'Speaker assertion','segment_ids':['s1']}]})}}]}
    result=summarize(project,'Entire podcast',[],'','','',Event(),lambda _:None,
                     completion=completion,provider_name='Offline fixture',chunk_limit=6000)
    assert ''.join(seen)==project.segments[0].text
    assert result[0].provider=='Offline fixture' and all(c.status=='supported' for c in result[0].claims)


def test_truncated_local_generation_is_refused():
    engine=LocalLLM.__new__(LocalLLM)
    engine.config=SimpleNamespace(context_size=8192,max_summary_tokens=1536)
    responses=iter([{'prompt':'p'},{'tokens':[]},{'choices':[{'finish_reason':'length'}]}])
    engine.request=lambda *a:next(responses)
    with pytest.raises(Exception,match='output limit'):
        engine.complete([{'role':'user','content':'[{"id":"s1","text":"evidence"}]'}])


@pytest.fixture
def server_bundle(tmp_path):
    import os,sys
    if os.name=='nt':
        pytest.skip('Executable script fixture is POSIX; portable protocol/unit tests still run on Windows')
    from podcast_studio.edge import EdgeConfig,digest
    script=tmp_path/'fixture-server'
    script.write_text('#!'+sys.executable+'\n'+'''import argparse,json,time
from http.server import BaseHTTPRequestHandler,HTTPServer
parser=argparse.ArgumentParser()
parser.add_argument('--port',type=int);parser.add_argument('--api-key-file');args,_=parser.parse_known_args()
key=open(args.api_key_file).read().strip()
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args): pass
 def send(self,data):
  self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(json.dumps(data).encode())
 def do_GET(self):
  if self.headers.get('Authorization')!='Bearer '+key:
   self.send_response(401);self.end_headers();return
  self.send({'data':[{'id':'fixture'}]})
 def do_POST(self):
  data=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
  if self.path=='/apply-template': self.send({'prompt':json.dumps(data['messages'])})
  elif self.path=='/tokenize': self.send({'tokens':[1]*len(data['content'].split())})
  else:
   records=json.loads(data['messages'][-1]['content'])
   if records[0]['text']=='BLOCKME':time.sleep(60)
   claim={'text':records[0]['text'],'segment_ids':[records[0]['id']],'ambiguous':False}
   self.send({'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'claims':[claim]})}}]})
HTTPServer(('127.0.0.1',args.port),Handler).serve_forever()
''')
    script.chmod(0o700)
    model=tmp_path/'fixture.gguf';model.write_bytes(b'protocol fixture, not trained weights')
    config=EdgeConfig('cpu',sys.executable,str(model),str(model),str(model),sys.executable,
                      summary_executable=str(script),summary_model=str(model))
    config.hashes={name:digest(getattr(config,name)) for name in config.asset_names()}
    path=tmp_path/'edge-config.json';config.save(path)
    return path


def test_owned_loopback_server_and_cleanup(server_bundle,project,monkeypatch):
    import requests
    original=requests.Session.request
    seen=[]
    def local_only(self,method,url,*args,**kwargs):
        assert url.startswith('http://127.0.0.1:')
        seen.append(url)
        assert self.trust_env is False
        return original(self,method,url,*args,**kwargs)
    monkeypatch.setattr(requests.Session,'request',local_only)
    with LocalLLM(server_bundle,Event(),lambda _:None) as engine:
        result=summarize(project,'Individual speakers',['a'],'','','',Event(),lambda _:None,
                         completion=engine.complete,provider_name='Offline protocol fixture')
        process=engine.process
        temporary=engine.directory.name
        assert result[0].claims[0].segment_ids==['s1']
    from pathlib import Path
    assert process.poll() is not None and not Path(temporary).exists() and seen


def test_cancel_during_blocked_local_inference(server_bundle):
    from threading import Timer
    from podcast_studio.providers import Cancelled
    import time
    event=Event()
    with LocalLLM(server_bundle,event,lambda _:None) as engine:
        timer=Timer(.4,event.set);timer.start()
        started=time.monotonic()
        with pytest.raises(Cancelled):
            engine.complete([{'role':'user','content':'[{"id":"s1","text":"BLOCKME"}]'}])
        timer.join()
        assert time.monotonic()-started<6
        assert engine.process.poll() is not None
