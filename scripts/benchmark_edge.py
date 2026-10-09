"""Run on the real target, with networking disconnected and prepared local assets."""
from pathlib import Path
from threading import Event, Thread
import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from podcast_studio.edge import EdgeConfig, OfflineTranscriber, write_json
from podcast_studio.model import Project
from podcast_studio.providers import apply_transcript
from podcast_studio.local_llm import local_summaries


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True);parser.add_argument('--audio',required=True)
    parser.add_argument('--output',required=True);parser.add_argument('--speakers',type=int,default=-1)
    parser.add_argument('--summaries',action='store_true');parser.add_argument('--resume',action='store_true')
    args=parser.parse_args()
    config=EdgeConfig.load(args.config).verify(summary=args.summaries)
    output=Path(args.output).resolve()
    if output.exists() and not args.resume:
        parser.error('Output exists; choose another path or explicitly pass --resume')
    project=Project.load(output) if args.resume and output.exists() else Project(Path(args.audio).stem,str(Path(args.audio).resolve()))
    cancel,monitor_done=Event(),Event()
    samples=[]
    def monitor():
        if not shutil.which('nvidia-smi'):return
        while not monitor_done.is_set():
            try:
                result=subprocess.run(['nvidia-smi','--query-gpu=memory.used','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=5,check=True)
                samples.extend(int(line.strip()) for line in result.stdout.splitlines())
            except (OSError,ValueError,subprocess.SubprocessError):pass
            monitor_done.wait(1)
    watcher=Thread(target=monitor,daemon=True);watcher.start()
    started=time.monotonic()
    try:
        provider=OfflineTranscriber(args.config,output.parent/'edge-cache',cancel)
        result=provider.transcribe(project,{'speakers_expected':args.speakers},lambda:project.save(output),print)
        apply_transcript(project,result);project.save(output)
        transcription_seconds=time.monotonic()-started
        summary_seconds=0
        if args.summaries:
            summary_started=time.monotonic()
            project.summaries.extend(local_summaries(project,'Entire podcast',[],'',args.config,cancel,print))
            summary_seconds=time.monotonic()-summary_started
            project.save(output)
        report={'profile':config.profile,'platform':platform.platform(),'machine':platform.machine(),
                'audio_seconds':project.duration,'transcription_seconds':transcription_seconds,'summary_seconds':summary_seconds,
                'transcription_real_time_factor':transcription_seconds/project.duration if project.duration else None,
                'peak_gpu_total_used_MiB':max(samples) if samples else None,
                'gpu_measurement_note':'nvidia-smi total device usage, including other applications; not isolated allocation',
                'speaker_ids':list(project.speakers),'segments':len(project.segments),'model_sha256':config.hashes,
                'accuracy_note':'No ground-truth comparison; these timings do not measure transcription/diarization accuracy'}
        write_json(output.with_suffix('.benchmark.json'),report)
        print(json.dumps(report,indent=2))
    except KeyboardInterrupt:
        cancel.set();project.save(output);raise
    finally:
        monitor_done.set();watcher.join(timeout=6)


if __name__=='__main__':
    main()
