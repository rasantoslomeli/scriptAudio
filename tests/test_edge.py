from pathlib import Path
from threading import Event, Timer
import json
import os
import sys
import wave
import pytest
from podcast_studio.edge import EdgeConfig, OfflineTranscriber, align_transcript, assign_speaker, digest, run_process, whisper_words, write_json
from podcast_studio.providers import Cancelled, apply_transcript
from podcast_studio.model import Project


@pytest.fixture
def bundle(tmp_path):
    model=tmp_path/'model.bin';model.write_bytes(b'explicit local test fixture, not trained weights')
    config=EdgeConfig('cpu',sys.executable,str(model),str(model),str(model),sys.executable,
                      summary_executable=sys.executable,summary_model=str(model),threads=2,chunk_seconds=30)
    config.hashes={name:digest(getattr(config,name)) for name in config.asset_names()}
    path=tmp_path/'edge-config.json';config.save(path)
    return path,config


def write_wav(path,seconds=80):
    with wave.open(str(path),'wb') as out:
        out.setparams((1,2,16000,0,'NONE','not compressed'))
        out.writeframes(b'\x00\x00'*16000*seconds)


def test_bundle_integrity_and_relative_paths(bundle,tmp_path):
    path,config=bundle
    assert EdgeConfig.load(path).verify(summary=True).profile=='cpu'
    raw=json.loads(path.read_text());raw['asr_model']='model.bin';write_json(path,raw)
    assert EdgeConfig.load(path).asr_model==str(tmp_path/'model.bin')
    (tmp_path/'model.bin').write_bytes(b'altered')
    with pytest.raises(ValueError,match='checksum'):
        EdgeConfig.load(path).verify()


def test_missing_assets_never_download(bundle,monkeypatch):
    import requests
    monkeypatch.setattr(requests.Session,'request',lambda *a,**k:pytest.fail('offline path attempted HTTP'))
    path,config=bundle
    Path(config.asr_model).unlink()
    with pytest.raises(ValueError,match='missing'):
        EdgeConfig.load(path).verify()


def test_attribution_withholds_ambiguous_voice():
    turns=[{'start':0,'end':2,'speaker':0},{'start':.5,'end':1.5,'speaker':1}]
    assert assign_speaker(.75,1.25,turns)==(None,True,['Ambiguous/overlapping voices; attribution withheld'])
    assert assign_speaker(0,.4,turns)==(0,False,[])
    assert assign_speaker(4,5,turns)[0] is None


def test_token_pieces_preserve_punctuation_and_original_text():
    item={'text':' Hello, collaboration.', 'offsets':{'from':0,'to':3000}, 'tokens':[
        {'text':' Hello','offsets':{'from':0,'to':800},'p':.9},
        {'text':',','offsets':{'from':800,'to':1000},'p':.8},
        {'text':' collab','offsets':{'from':1000,'to':1600},'p':.9},
        {'text':'oration.','offsets':{'from':1600,'to':3000},'p':.95},
        {'text':'[_EOT_]'}]}
    words=whisper_words(item)
    assert [w['text'] for w in words]==[' Hello,',' collaboration.']
    assert ''.join(w['text'] for w in words)==item['text']
    item['tokens'][0].pop('offsets')
    assert whisper_words(item)==[]


def test_chunk_overlap_ownership_and_returning_speaker():
    # Both overlapping decodes contain the same boundary words; midpoint assigns each to one core.
    item={'text':' first second', 'offsets':{'from':29000,'to':31000},'tokens':[
        {'text':' first','offsets':{'from':29000,'to':30000},'p':.9},
        {'text':' second','offsets':{'from':30000,'to':31000},'p':.9}]}
    other={'text':' first second','offsets':{'from':4000,'to':6000},'tokens':[
        {'text':' first','offsets':{'from':4000,'to':5000},'p':.9},
        {'text':' second','offsets':{'from':5000,'to':6000},'p':.9}]}
    chunks=[{'offset':0,'core_start':0,'core_end':30,'data':{'transcription':[item]}},
            {'offset':25,'core_start':30,'core_end':60,'data':{'transcription':[other]}}]
    turns=[{'start':0,'end':30,'speaker':7},{'start':30,'end':60,'speaker':9}]
    result=align_transcript(chunks,turns,60)
    assert [u['text'] for u in result['utterances']]==['first','second']
    assert [u['speaker'] for u in result['utterances']]==['A','B']
    assert [u['start'] for u in result['utterances']]==[29000,30000]


def test_full_offline_pipeline_resume_and_fingerprint(bundle,tmp_path,monkeypatch):
    import requests
    monkeypatch.setattr(requests.Session,'request',lambda *a,**k:pytest.fail('offline transcription attempted HTTP'))
    source=tmp_path/'original.wav';write_wav(source)
    original_hash=digest(source)
    project=Project('Offline fixture',str(source))
    calls=[]
    def process(command,cancel,progress,label,log_path,progress_file=None):
        calls.append(label)
        if 'normalization' in label:
            write_wav(command[-1])
        elif 'diarization' in label:
            write_json(command[-2],[{'start':0,'end':40,'speaker':7},{'start':40,'end':80,'speaker':9}])
        else:
            prefix=command[command.index('-of')+1]
            offset=0 if label.endswith('1/3') else 5000
            text=' Hello local world.'
            tokens=[{'text':word,'offsets':{'from':offset+i*1000,'to':offset+(i+1)*1000},'p':.9}
                    for i,word in enumerate([' Hello',' local',' world.'])]
            write_json(prefix+'.json',{'transcription':[{'text':text,'offsets':{'from':offset,'to':offset+3000},'tokens':tokens}]})
    monkeypatch.setattr('podcast_studio.edge.run_process',process)
    provider=OfflineTranscriber(bundle[0],tmp_path/'cache',Event())
    states=[]
    result=provider.transcribe(project,{},lambda:states.append(project.job.status),lambda _:None)
    assert len(calls)==5 and 'diarizing' in states
    assert len(result['utterances'])==3
    again=provider.transcribe(project,{},lambda:None,lambda _:None)
    assert result==again and len(calls)==5  # Completed stages survive a restart.
    apply_transcript(project,result)
    assert project.speakers['speaker_A'].name=='Speaker A'
    assert project.segments[0].speaker_id==project.segments[1].speaker_id
    assert project.segments[2].speaker_id=='speaker_B'
    assert project.original_segments and project.segments[0].words
    assert digest(source)==original_hash
    provider.transcribe(project,{'language_code':'es'},lambda:None,lambda _:None)
    assert len(calls)==10  # Different settings cannot reuse old recognition.


def test_local_child_cancellation_is_reaped(tmp_path):
    event=Event()
    timer=Timer(.4,event.set);timer.start()
    started=__import__('time').monotonic()
    with pytest.raises(Cancelled):
        run_process([sys.executable,'-c','import time; time.sleep(60)'],event,lambda _:None,'fixture engine',tmp_path/'engine.log')
    timer.join()
    assert __import__('time').monotonic()-started<6


def test_default_gui_is_offline_and_no_key_prompt(qtbot,tmp_path,monkeypatch):
    from podcast_studio.app import Studio
    window=Studio(tmp_path/'state');qtbot.addWidget(window)
    window.add_project(Project('No asset fixture'))
    messages=[]
    monkeypatch.setattr(window,'fail',messages.append)
    monkeypatch.setattr(window,'cloud_consent',lambda **k:pytest.fail('offline default requested cloud consent'))
    monkeypatch.setattr(window,'configure',lambda:pytest.fail('offline default requested API keys'))
    window.process_current()
    assert not window.busy() and messages and 'No recording has been uploaded' in messages[0]
    window.close()


def test_gpu_profile_cannot_claim_cpu_execution(tmp_path):
    from podcast_studio.edge import check_acceleration
    from podcast_studio.providers import ProviderError
    log=tmp_path/'backend.log';log.write_text('whisper_backend_init_gpu: no GPU found')
    with pytest.raises(ProviderError,match='CUDA'):
        check_acceleration('rtx3080',log)
    log.write_text('whisper_backend_init_gpu: using Metal backend')
    check_acceleration('apple-silicon',log)


def test_split_gguf_requires_every_local_shard(bundle,tmp_path):
    path,config=bundle
    first=tmp_path/'model-00001-of-00002.gguf';first.write_bytes(b'first part')
    config.summary_model=str(first);config.hashes['summary_model']=digest(first);config.save(path)
    with pytest.raises(ValueError,match='every GGUF shard'):
        EdgeConfig.load(path).verify(summary=True)
    shard=tmp_path/'model-00002-of-00002.gguf';shard.write_bytes(b'second part')
    config.summary_shards={shard.name:digest(shard)};config.save(path)
    EdgeConfig.load(path).verify(summary=True)
    shard.write_bytes(b'tampered part')
    with pytest.raises(ValueError,match='shard'):
        EdgeConfig.load(path).verify(summary=True)
    shard.unlink()
    with pytest.raises(ValueError,match='shard'):
        EdgeConfig.load(path).verify(summary=True)


def test_long_recording_interval_index_skips_expired_turns():
    from podcast_studio.edge import TurnIndex
    turns=[{'start':i*2,'end':i*2+1,'speaker':i%3} for i in range(10000)]
    index=TurnIndex(turns)
    assert index.overlapping(19998.1,19998.9)==[turns[-1]]
    assert assign_speaker(19998.1,19998.9,index)[0]==turns[-1]['speaker']
    assert index.overlapping(19999.1,19999.9)==[]
