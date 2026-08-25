"""The supervisor's second non-bank tool: standing orders the user gave it.

"Her sabah 09:00'da altın fiyatlarını bankalar arasında karşılaştır ve bana bir
rapor ver." That is a request the assistant cannot answer in the turn it is
asked, because most of the answer does not exist yet. So it stores the request
and the hour, and `api/automations/` runs it every morning and leaves a report.

Two tools, and the asymmetry is deliberate:

  create_automation   writes one
  list_automations    reads them back

**There is no delete tool.** Deleting the wrong automation is not recoverable and
the model has no way to be sure which one the user meant -- two automations about
gold prices differ only in their wording. The list in the UI is two clicks away
and has a delete button per row, so nothing is lost by keeping that a human
action.

**The schedule is three integers, never a cron string.** A wrong cron fails the
worst way available: silently, by simply never firing, with nothing on screen to
show that it did not. `hour`/`minute`/`weekdays` fail visibly instead -- the
profile page renders "Her gün 09:00" and the user can see that is not what they
asked for. That matters more than usual here: this model has already ignored
three prompt-only instructions in this codebase, so anything it must not get
wrong belongs in the tool's shape rather than in its description.

Nothing here raises. `api/routers/chat.py` discards the whole assembled answer
when it sees an `error` frame, so an exception in a tool would delete a good
answer along with the failed write. A refusal is a sentence, following
`api/saved_tables.py::save_table_view` and `banks/tools.py::_answer`.
"""

import logging
import uuid

from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from sqlalchemy import select

from .runtime import AgentContext

logger = logging.getLogger(__name__)

# NOTE: this module must NOT use `from __future__ import annotations`.
#
# LangChain decides whether to inject `ToolRuntime` by *inspecting the annotation
# object* on the tool function. The future import turns every annotation into a
# string, so `runtime: ToolRuntime[AgentContext]` arrives as the literal text
# `"ToolRuntime[AgentContext]"`, no injection happens, and `runtime` stays a
# required positional argument the model was never told about. The failure is
# not a type error at import: the tool builds, binds and is offered to the model
# normally, and only blows up the first time it is actually called --
# `TypeError: create_automation() missing 1 required positional argument`, which
# `_agent_answer` turns into "The live banking assistant is unavailable."
#
# Measured on 2026-08-25, on the first real chat turn that asked for one.
# `agents/shared/agent_tools.py` has no future import for the same reason.

#: How many automations one user may have. Not a licensing limit -- each one is a
#: full supervisor pass over ten banks on a schedule the user does not watch, and
#: a model that misreads "every day" as "every automation" should hit a wall it
#: can report rather than filling a table.
MAX_PER_USER = 20

TITLE_CHARS = 160

_WEEKDAY_NAMES = ("Pazartesi", "Salı", "Çarşamba", "Perşembe", "Cuma", "Cumartesi", "Pazar")


class CreateAutomationInput(BaseModel):
    title: str = Field(
        description=(
            "Short name for this standing order, in the user's language, as it "
            "will appear in their list: 'Sabah altın raporu'. Not a sentence."
        )
    )
    prompt: str = Field(
        description=(
            "The question to ask every time it runs, written as if the user were "
            "asking it fresh -- it will be read with no memory of this "
            "conversation. Include the banks, products, currencies and the shape "
            "of the answer they asked for. Write it in the user's language."
        )
    )
    hour: int = Field(
        ge=0,
        le=23,
        description=(
            "Hour it runs, 0-23, Turkey local time. If the user said 'sabah' "
            "without an hour use 9; 'akşam' 20; 'gece' 22. Ask them instead if "
            "the report is time-critical."
        ),
    )
    minute: int = Field(default=0, ge=0, le=59, description="Minute, 0-59.")
    weekdays: list[int] = Field(
        default_factory=list,
        description=(
            "Which days: 0=Monday ... 6=Sunday. Leave EMPTY for every day, which "
            "is what 'her gün' and 'her sabah' mean. Only list days when the user "
            "named them ('hafta içi' is [0,1,2,3,4])."
        ),
    )
    web_search: bool = Field(
        default=True,
        description=(
            "Allow live internet research on each run. Keep true unless the user "
            "asked for indexed data only -- a report about what changed since "
            "yesterday cannot be answered from the offline index."
        ),
    )


class ListAutomationsInput(BaseModel):
    """No arguments.

    Declared rather than left to inference: `StructuredTool.from_function` builds
    a schema from the signature, and the signature carries `runtime:
    ToolRuntime`, which has no JSON schema -- so inference raises
    `PydanticInvalidForJsonSchema` instead of producing an empty object. Every
    tool in this codebase passes `args_schema` explicitly for the same reason.
    """


def _describe(hour: int, minute: int, weekdays: list[int]) -> str:
    when = f"{hour:02d}:{minute:02d}"
    if not weekdays:
        return f"her gün {when}"
    return f"{', '.join(_WEEKDAY_NAMES[d] for d in weekdays)} günleri {when}"


def _user_id(runtime: ToolRuntime[AgentContext]) -> uuid.UUID | None:
    raw = (runtime.context or {}).get("user_id")
    if not raw:
        return None
    try:
        return uuid.UUID(str(raw))
    except (ValueError, AttributeError, TypeError):
        logger.warning("automation tool got an unusable user_id %r", raw)
        return None


def build_automation_tools() -> list[StructuredTool]:
    """The pair, built together because they share the storage and the refusal.

    Imports of `api.*` happen inside the callables. `agents` is imported by the
    API rather than the other way round, and pulling the ORM in at module scope
    here would make that a cycle.
    """

    def create_automation(
        title: str,
        prompt: str,
        hour: int,
        runtime: ToolRuntime[AgentContext],
        minute: int = 0,
        weekdays: list[int] | None = None,
        web_search: bool = True,
    ) -> str:
        from api.automations.schedule import next_run, valid_weekdays
        from api.db.base import utcnow
        from api.db.models import Automation
        from api.db.session import session_scope

        user_id = _user_id(runtime)
        if user_id is None:
            # Not a bug and not the user's fault: the standalone/legacy answer
            # path has no signed-in user. Saying so plainly is better than an
            # automation nobody owns, which could never run.
            return (
                "Bu oturumda otomasyon kuramıyorum çünkü hesap bilgisi yok. "
                "Kullanıcıya giriş yapıp Profil sayfasından kurmasını söyle."
            )

        days = valid_weekdays(weekdays)
        clean_title = (title or "").strip()[:TITLE_CHARS]
        clean_prompt = (prompt or "").strip()
        if not clean_title or not clean_prompt:
            return (
                "Otomasyon kurulamadı: başlık ve çalıştırılacak istek boş "
                "olamaz. Kullanıcıdan ne istediğini netleştir."
            )

        try:
            with session_scope() as store:
                existing = store.scalars(
                    select(Automation).where(Automation.user_id == user_id)
                ).all()
                if len(existing) >= MAX_PER_USER:
                    return (
                        f"Kullanıcının zaten {len(existing)} otomasyonu var, üst "
                        f"sınır {MAX_PER_USER}. Yeni bir tane kurmak için Profil "
                        "sayfasından birini silmesi gerekiyor."
                    )
                row = Automation(
                    user_id=user_id,
                    title=clean_title,
                    prompt=clean_prompt,
                    hour=hour,
                    minute=minute,
                    weekdays=days,
                    web_search=bool(web_search),
                    enabled=True,
                    next_run_at=next_run(utcnow(), hour, minute, days),
                )
                store.add(row)
                store.flush()
                created_id = row.id
                first = row.next_run_at
        except Exception as exc:  # noqa: BLE001 - a traceback must not reach the model
            logger.exception("create_automation failed user=%s", user_id)
            return (
                f"Otomasyon kaydedilemedi ({type(exc).__name__}). Kullanıcıya "
                "kurulamadığını söyle; aynı bilgilerle tekrar DENEME."
            )

        logger.info(
            "automation created id=%s user=%s title=%r schedule=%s first=%s",
            created_id,
            user_id,
            clean_title,
            _describe(hour, minute, days),
            first.isoformat(),
        )
        return (
            f"Otomasyon kuruldu: {clean_title!r}, {_describe(hour, minute, days)}. "
            f"İlk çalışma: {first.astimezone().strftime('%d.%m.%Y %H:%M')}. "
            "Kullanıcıya kurulduğunu ve hangi saatte çalışacağını söyle. "
            "Raporlar 'Profil > Raporlar' sayfasında birikir; otomasyonun "
            "kendisi 'Profil > Genel' sayfasından değiştirilir veya silinir -- "
            "bunlar FARKLI sayfalar, karıştırma. Bildirim zili okunmamış raporu "
            "gösterir. Saati yanlış anlamış olabileceğini de belirt."
        )

    def list_automations(runtime: ToolRuntime[AgentContext]) -> str:
        from api.db.models import Automation
        from api.db.session import session_scope

        user_id = _user_id(runtime)
        if user_id is None:
            return "Hesap bilgisi olmadığı için otomasyon listesini okuyamıyorum."
        try:
            with session_scope() as store:
                rows = store.scalars(
                    select(Automation)
                    .where(Automation.user_id == user_id)
                    .order_by(Automation.created_at)
                ).all()
                lines = [
                    f"{i}. {r.title} -- {_describe(r.hour, r.minute, r.weekdays)}"
                    + ("" if r.enabled else "  (DURDURULMUŞ)")
                    for i, r in enumerate(rows, 1)
                ]
        except Exception as exc:  # noqa: BLE001
            logger.exception("list_automations failed user=%s", user_id)
            return f"Otomasyon listesi okunamadı ({type(exc).__name__})."
        if not lines:
            return "Kullanıcının kurulu otomasyonu yok."
        return "\n".join(lines) + (
            "\n\nSilmek veya saatini değiştirmek Profil sayfasından yapılır; "
            "senin silme yetkin yok."
        )

    return [
        StructuredTool.from_function(
            func=create_automation,
            name="create_automation",
            description=(
                "Set up a recurring report for the user: a question you will be "
                "asked automatically at a time of day they choose, whose answer "
                "is saved to their Reports page and announced by the "
                "notification bell. Use this when the user asks for something "
                "repeating -- 'her sabah', 'her gün 9'da', 'her pazartesi', "
                "'bana günlük rapor ver'. Do NOT use it for a question they want "
                "answered now; answer that normally. Storing the request is all "
                "this does -- it retrieves nothing and proves nothing, so still "
                "answer today's version of the question from the bank "
                "specialists if they asked for one."
            ),
            args_schema=CreateAutomationInput,
        ),
        StructuredTool.from_function(
            func=list_automations,
            name="list_automations",
            description=(
                "List the recurring reports this user already has, with their "
                "schedules. Use it when they ask what is set up, or before "
                "creating one that sounds like something they may already have. "
                "It cannot change or delete anything."
            ),
            args_schema=ListAutomationsInput,
        ),
    ]
