"""Aktif banka motorunu (download_sites.<slug>) çalışma zamanında seçmeyi sağlar.

crawl/* modülleri `from dataprep.crawl.bank import engine` der ve engine.fetch(...),
engine.CONFIG, engine.write_doc(...) gibi çağrıları kullanır. Hangi bankanın
motoru olduğuna `engine.load(slug)` karar verir. Böylece tek crawl paketi 10
bankanın hepsini çalıştırır; motor dosyaları (format + PDF mantığı) değişmez.
"""
from __future__ import annotations

import importlib


class _EngineProxy:
    """download_sites.<slug> modülüne saydam yönlendirme yapan vekil.

    Öznitelik okuma/yazma doğrudan yüklü modüle gider; böylece engine.OUT gibi
    modül-global durumlar (write_doc/url_to_path'in okuduğu) doğru yere yazılır.
    """

    def load(self, slug: str):
        mod = importlib.import_module(f"dataprep.crawl.engines.{slug}")
        object.__setattr__(self, "_mod", mod)
        return mod

    def __getattr__(self, name):
        mod = self.__dict__.get("_mod")
        if mod is None:
            raise RuntimeError("engine yüklenmedi — önce engine.load(slug) çağır")
        return getattr(mod, name)

    def __setattr__(self, name, value):
        mod = self.__dict__.get("_mod")
        if mod is None:
            raise RuntimeError("engine yüklenmedi — önce engine.load(slug) çağır")
        setattr(mod, name, value)


engine = _EngineProxy()
