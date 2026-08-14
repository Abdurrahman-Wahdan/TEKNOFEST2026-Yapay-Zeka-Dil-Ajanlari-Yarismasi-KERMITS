"""Server'a aynı anda açık istek sayısını sınırlayan PAYLAŞILAN semafor.

Server ~100 açık bağlantıda tıkanıyor. Hem Gemma (vlm.py, doğrudan httpx) hem Qwen
(dataprep.compare.bank_agent, langchain ChatOpenAI — her çağrıda kendi bağlantısını
açar, havuzsuz) AYNI server'ı paylaşıyor. İkisi ayrı ayrı kendi tavanlarını tutarsa
toplam kolayca 100'ü aşar; bu yüzden TEK bir semaforda buluşurlar.
"""
import os
import threading

NET_SEM = threading.Semaphore(int(os.environ.get("COMPARE_NET_SEM", "90")))
