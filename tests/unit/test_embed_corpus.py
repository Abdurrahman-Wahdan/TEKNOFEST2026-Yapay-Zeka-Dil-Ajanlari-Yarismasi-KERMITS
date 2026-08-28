"""Reading the corpus into Qdrant.

Every case here came out of running the thing against the real corpus and the
live collection, because none of these failures raise: they write a row that is
subtly wrong and nothing complains until someone follows a citation.
"""


from dataprep import embed


class TestFrontMatter:
    """`---` is the front-matter delimiter *and* a legal substring of a URL."""

    def test_a_url_containing_the_delimiter_survives(self):
        """The regression. `text.split("---", 2)` split inside the URL: the url
        came back truncated at `...anlasmali-kurumlar-listesi` and the body
        began with `kolej.pdf"` followed by the rest of the front matter as
        prose. 25 files would have been embedded under a URL that 404s -- and
        6 such URLs were already in the live collection from the pipeline that
        has the same bug."""
        text = (
            '---\n'
            'url: "https://a.example/anlasmali-kurumlar-listesi---kolej.pdf"\n'
            'content_relevance: "gerekli"\n'
            '---\n\n'
            '# Anlaşmalı Üniversiteler\n\nGövde burada.\n'
        )
        fm, body = embed._parse(text)
        assert fm["url"] == "https://a.example/anlasmali-kurumlar-listesi---kolej.pdf"
        assert fm["content_relevance"] == "gerekli"
        assert body.startswith("# Anlaşmalı Üniversiteler")
        assert "kolej.pdf" not in body

    def test_a_horizontal_rule_in_the_body_is_not_a_delimiter(self):
        text = '---\nurl: "https://a.example/x"\n---\n\nÜst\n\n---\n\nAlt\n'
        fm, body = embed._parse(text)
        assert fm["url"] == "https://a.example/x"
        assert "Üst" in body and "Alt" in body

    def test_no_front_matter_returns_the_whole_text(self):
        fm, body = embed._parse("# Başlık\n\nGövde.\n")
        assert fm == {}
        assert body.startswith("# Başlık")

    def test_an_unterminated_front_matter_is_not_parsed(self):
        """Better to treat it as body than to swallow the whole file as keys."""
        fm, body = embed._parse('---\nurl: "https://a.example/x"\n\n# Başlık\n')
        assert fm == {}


class TestPointId:
    """Ids must be a function of the content's identity, not of when it ran."""

    def test_the_same_chunk_always_gets_the_same_id(self):
        """langchain's random uuid4 per document is why 1051 of the tables'
        3378 point_id citations resolve to nothing."""
        a = embed.point_id("https://a.example/x", 0)
        b = embed.point_id("https://a.example/x", 0)
        assert a == b

    def test_different_chunks_of_one_page_differ(self):
        assert embed.point_id("https://a.example/x", 0) != embed.point_id("https://a.example/x", 1)

    def test_different_pages_differ(self):
        assert embed.point_id("https://a.example/x", 0) != embed.point_id("https://a.example/y", 0)

    def test_a_truncated_url_is_a_different_id(self):
        """Guards the parser bug from the other side: were it to come back, the
        affected pages would land on new ids rather than overwriting."""
        full = embed.point_id("https://a.example/listesi---kolej.pdf", 0)
        cut = embed.point_id("https://a.example/listesi", 0)
        assert full != cut


class TestIterDocs:
    @staticmethod
    def _site(tmp_path, monkeypatch, files):
        site = tmp_path / "testbank_site" / "content"
        site.mkdir(parents=True)
        for name, text in files.items():
            (site / name).write_text(text, encoding="utf-8")
        monkeypatch.setattr(embed, "CORPUS", tmp_path)
        embed.EMPTY.clear()
        return list(embed.iter_docs("testbank", {}))

    def test_nothing_is_excluded_for_relevance(self, tmp_path, monkeypatch):
        """The pipeline already decided relevance -- 828 documents it judged
        `gereksiz` were never written to disk at all. Anything that DID reach
        `content/` is text the pipeline chose to keep, so the embedder does not
        get a second vote on it."""
        docs = self._site(tmp_path, monkeypatch, {
            "a.md": '---\nurl: "https://a/1"\ncontent_relevance: "gereksiz"\n---\n\n' + "x" * 200,
        })
        assert len(docs) == 1

    def test_an_empty_body_is_recorded_not_silently_dropped(self, tmp_path, monkeypatch):
        """A scanned signature PDF the cleaner got no text out of. There is
        nothing to embed, but "we covered everything" and "we never looked at
        this file" must not read the same in the run report."""
        docs = self._site(tmp_path, monkeypatch, {
            "a.md": '---\nurl: "https://a/empty"\ncontent_relevance: "gerekli"\n---\n\n',
        })
        assert docs == []
        assert "https://a/empty" in embed.EMPTY

    def test_metadata_matches_the_live_collection_schema(self, tmp_path, monkeypatch):
        """Writing different key names is adding rows the reader cannot see --
        `retrieval.py` looks for exactly these."""
        docs = self._site(tmp_path, monkeypatch, {
            "a.md": '---\nurl: "https://a/1"\ncontent_relevance: "gerekli"\n---\n\n' + "gövde " * 40,
        })
        assert len(docs) == 1
        key, pid, text, meta = docs[0]
        assert key == ("metin", "https://a/1")
        assert pid == embed.point_id("https://a/1", 0)
        assert set(meta) == {"bank", "url", "type", "chunk_index", "validity_status"}
        assert meta["bank"] == "testbank" and meta["type"] == "metin"

    def test_the_url_pool_supplies_validity_the_file_lacks(self, tmp_path, monkeypatch):
        site = tmp_path / "testbank_site" / "content"
        site.mkdir(parents=True)
        (site / "a.md").write_text(
            '---\nurl: "https://a/1"\ncontent_relevance: "gerekli"\n---\n\n' + "gövde " * 40,
            encoding="utf-8")
        monkeypatch.setattr(embed, "CORPUS", tmp_path)
        embed.EMPTY.clear()
        pool = {"https://a/1": {"validity_status": "suresi_gecmis",
                                "gecerlilik_bitis": "2020-01-01"}}
        _key, _pid, _text, meta = next(iter(embed.iter_docs("testbank", pool)))
        assert meta["validity_status"] == "suresi_gecmis"
        assert meta["gecerlilik_bitis"] == "2020-01-01"


def test_there_is_no_minimum_chunk_size():
    """Every floor was removed: the pipeline's output goes in as it stands."""
    assert embed.MIN_CHUNK == 0 and embed.MIN_IMG_CHUNK == 0
    assert embed._chunks("kısa") == ["kısa"]


def test_chunking_matches_the_size_the_collection_was_built_with():
    """Measured on the live collection: median 1391 chars, p95 8171, max 9017,
    and 3894 of 5081 urls in a single chunk. The old 900 would have cut the same
    page into ten pieces that look nothing like their neighbours."""
    assert embed.CHUNK == 9000
    assert len(embed._chunks("tek paragraf. " * 100)) == 1


class TestImageBlocks:
    """`<!-- görsel: URL -->` blocks are their own points, not page text.

    Measured on the live collection: 3000 sampled `metin` points, not one
    containing a görsel block, against 1308 separate `gorsel` points. Leaving
    the blocks in the page body would index the same text under two shapes.
    """

    def test_images_are_split_out_of_the_page_text(self):
        body = ("Sayfa metni burada.\n\n"
                "<!-- görsel: https://a/kampanya.jpg -->\n"
                "Toplam 5.000TL'ye kadar bonus kazan!\n")
        clean, images = embed.split_images(body)
        assert clean == "Sayfa metni burada."
        assert images == [("https://a/kampanya.jpg", "Toplam 5.000TL'ye kadar bonus kazan!")]

    def test_several_blocks_each_keep_their_own_text(self):
        body = ("<!-- görsel: https://a/1.jpg -->\nBirinci\n\n"
                "<!-- görsel: https://a/2.jpg -->\nİkinci\n")
        clean, images = embed.split_images(body)
        assert clean == ""
        assert [u for u, _ in images] == ["https://a/1.jpg", "https://a/2.jpg"]
        assert [t for _, t in images] == ["Birinci", "İkinci"]

    def test_a_page_with_no_images_is_untouched(self):
        clean, images = embed.split_images("Sadece metin.")
        assert clean == "Sadece metin." and images == []

    def test_an_image_becomes_a_gorsel_record(self, tmp_path, monkeypatch):
        site = tmp_path / "testbank_site" / "content"
        site.mkdir(parents=True)
        (site / "a.md").write_text(
            '---\nurl: "https://a/sayfa"\ncontent_relevance: "gerekli"\n---\n\n'
            + "gövde " * 40 + "\n\n<!-- görsel: https://a/afis.jpg -->\n" + "kampanya metni " * 5,
            encoding="utf-8")
        monkeypatch.setattr(embed, "CORPUS", tmp_path)
        embed.EMPTY.clear(); embed.SHORT.clear()
        docs = list(embed.iter_docs("testbank", {}))
        kinds = {key[0] for key, *_ in docs}
        assert kinds == {"metin", "gorsel"}
        gorsel = [d for d in docs if d[0][0] == "gorsel"][0]
        assert gorsel[0] == ("gorsel", "https://a/afis.jpg")
        assert gorsel[3]["gorsel_kaynak"] == "https://a/afis.jpg"
        assert gorsel[3]["url"] == "https://a/sayfa"      # the page it came from
        assert "kampanya metni" in gorsel[2]

    def test_even_the_shortest_caption_is_embedded(self, tmp_path, monkeypatch):
        """No length floor. An 8-character banner ('ÜCRETSİZ') is still text the
        pipeline's vision model extracted, and dropping it is a preprocessing
        decision the embedder has no business making."""
        site = tmp_path / "testbank_site" / "content"
        site.mkdir(parents=True)
        (site / "a.md").write_text(
            '---\nurl: "https://a/sayfa"\ncontent_relevance: "gerekli"\n---\n\n'
            + "gövde " * 40 + "\n\n<!-- görsel: https://a/afis.jpg -->\nÜCRETSİZ\n",
            encoding="utf-8")
        monkeypatch.setattr(embed, "CORPUS", tmp_path)
        embed.EMPTY.clear(); embed.SHORT.clear()
        docs = list(embed.iter_docs("testbank", {}))
        assert {key[0] for key, *_ in docs} == {"metin", "gorsel"}
        gorsel = [d for d in docs if d[0][0] == "gorsel"][0]
        assert gorsel[2].strip() == "ÜCRETSİZ"
