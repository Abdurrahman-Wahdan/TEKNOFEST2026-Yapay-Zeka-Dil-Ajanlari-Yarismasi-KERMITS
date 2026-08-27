"""vLLM'e "geçerli JSON üret" zorlaması (kısıtlı çözümleme / constrained decoding).

NEDEN VAR: aşama 1-4 (dataprep/vlm.py) her isteğe
`response_format: {"type": "json_object"}` gönderiyordu; vLLM bu bayrakla
üretimi gramere zorlar, yani BOZUK JSON FİZİKSEL OLARAK ÜRETİLEMEZ. Tablo
aşaması ise LangChain (get_llm) üzerinden gidiyordu ve bu bayrağı hiç
göndermiyordu — model serbest üretiyor, arada bir metin değerinin içindeki
tırnağı kaçırmayı unutuyordu:

    {"deger": "yıllık ücret "sıfır" olarak uygulanır"}

Bu hata DETERMİNİSTİK olduğu için sıcaklık merdiveni (0.0→1.0) ve somut
feedback ("Expecting ',' delimiter") de kurtaramıyordu: model 0.0'da da
1.0'da da aynı metni aynı biçimde yazıyor, feedback'i alınca da virgül
aramaya çalışıp asıl sorunu (tırnak) göremiyordu. 8 denemenin 8'i de aynı
duvara toslayıp chunk SESSİZCE ATLANIYORDU.

ARAÇ ÇAĞRISIYLA BİRLİKTE KULLANILMAZ: model JSON'a zorlanınca tool_calls
üretemez. Bu yüzden yalnız araçların BAĞLI OLMADIĞI, yani düz JSON beklenen
çağrılarda uygulanır (`if allow_tools and tools` dallanmasının else tarafı).
"""
from __future__ import annotations

# ChatOpenAI'nin sunucuya ilettiği ek gövde alanları.
JSON_KWARGS = {"model_kwargs": {"response_format": {"type": "json_object"}}}


def llm_kwargs(json_zorla: bool) -> dict:
    """JSON bekleyen çağrılar için ek get_llm parametreleri."""
    return dict(JSON_KWARGS) if json_zorla else {}
