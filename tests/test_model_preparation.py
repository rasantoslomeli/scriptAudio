import importlib.util
from pathlib import Path
import hashlib
import io
import pytest

spec=importlib.util.spec_from_file_location('prepare_models',Path(__file__).parents[1]/'scripts/prepare_models.py')
prepare=importlib.util.module_from_spec(spec);spec.loader.exec_module(prepare)


class Source(io.BytesIO):
    url='https://model-source.example/weights'
    headers={'Content-Length':'13'}


def test_verified_preparation_preserves_destination_on_failure(tmp_path,monkeypatch):
    target=tmp_path/'model.gguf';target.write_bytes(b'previous model')
    monkeypatch.setattr(prepare.urllib.request,'urlopen',lambda *a,**k:Source(b'new model'))
    asset={'filename':target.name,'url':Source.url,'sha256':hashlib.sha256(b'expected model').hexdigest()}
    with pytest.raises(ValueError,match='checksum'):
        prepare.download_verified(asset,tmp_path)
    assert target.read_bytes()==b'previous model' and not list(tmp_path.glob('*.partial'))
    asset['sha256']=hashlib.sha256(b'new model').hexdigest()
    assert prepare.download_verified(asset,tmp_path)==target
    assert target.read_bytes()==b'new model'


def test_verified_cache_does_not_contact_internet(tmp_path,monkeypatch):
    target=tmp_path/'model.gguf';target.write_bytes(b'known model')
    monkeypatch.setattr(prepare.urllib.request,'urlopen',lambda *a,**k:pytest.fail('cached model triggered network'))
    asset={'filename':target.name,'url':Source.url,'sha256':hashlib.sha256(b'known model').hexdigest()}
    prepare.download_verified(asset,tmp_path)
