"""Isolated CPU diarization worker: one global clustering across the recording."""
from pathlib import Path
import sys
import wave
from .edge import EdgeConfig, write_json


def main(arguments=None):
    config_path,audio_path,output_path,count = arguments or sys.argv[1:]
    config = EdgeConfig.load(config_path)
    import numpy as np
    import sherpa_onnx
    output_path = Path(output_path)
    floats = output_path.with_suffix('.f32')
    try:
        with wave.open(audio_path) as source:
            frames = source.getnframes()
            if frames > 16000*24*3600 or source.getframerate()!=16000 or source.getnchannels()!=1 or source.getsampwidth()!=2:
                raise ValueError('Diarization requires mono PCM16 16 kHz audio, at most 24 hours')
            with floats.open('wb') as target:
                while data := source.readframes(16000*30):
                    target.write((np.frombuffer(data,dtype='<i2').astype(np.float32)/32768).tobytes())
        samples = np.memmap(floats,mode='r',dtype=np.float32,shape=(frames,))
        diar_config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
            segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
                pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=config.segmentation_model,window_shift_ratio=.1),
                num_threads=config.threads,provider='cpu'),
            embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=config.embedding_model,num_threads=config.threads,provider='cpu'),
            clustering=sherpa_onnx.FastClusteringConfig(num_clusters=int(count),threshold=config.cluster_threshold),
            min_duration_on=.2,min_duration_off=.3)
        if not diar_config.validate():
            raise ValueError('Invalid local diarization models')
        engine = sherpa_onnx.OfflineSpeakerDiarization(diar_config)
        def progress(done,total):
            write_json(output_path.with_name('diarization.progress.json'),{'done':done,'total':total})
            return 0
        turns = engine.process(samples,callback=progress).sort_by_start_time()
        write_json(output_path,[{'start':float(t.start),'end':float(t.end),'speaker':int(t.speaker)} for t in turns])
        del samples
    finally:
        floats.unlink(missing_ok=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
