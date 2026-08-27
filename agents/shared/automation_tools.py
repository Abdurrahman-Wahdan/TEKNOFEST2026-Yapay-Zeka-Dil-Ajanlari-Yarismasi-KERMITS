"""The supervisor's second non-bank tool: standing orders the user gave it.

"Her sabah 09:00'da altın fiyatlarını bankalar arasında karşılaştır ve bana bir
rapor ver." That is a request the assistant cannot answer in the turn it is
asked, because most of the answer does not exist yet. So it stores the request
and the hour, and `api/automations/` runs it every morning and leaves a report.

Three tools, and the asymmetry is deliberate:

  create_automation   writes one
  update_automation   changes one that exists
  list_automations    reads them back

**`update_automation` exists because the assistant already invites the
correction.** `create_automation` tells the model to warn that it may have
misread the hour -- and for a while the model did exactly that, then had no way
to act when the user said "no, 19:00". It could only send them to the profile
page to fix something it had just got wrong in the same breath. An edit is also
the safe half of management: every field it touches is visible in the list and
editable there, and nothing is destroyed.

**There is still no delete tool.** Deleting the wrong automation is not
recoverable, and the model has no way to be sure which one the user meant -- two
automations about gold prices differ only in their wording. Pausing is the
reversible version of the same intent and `update_automation` can do it
(`enabled=false`), so "cancel my morning report" has an answer that cannot lose
anything. The list in the UI has a delete button per row.

**The schedule is typed fields, never a cron string.** Fixed schedules use
`hour`/`minute`/`weekdays`; recurring schedules use `interval_minutes`, with an
optional daily window. A wrong cron fails the
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
from typing import Literal

from langchain.tools import ToolRuntime
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from sqlalchemy import select

from .clock import TZ
from .runtime import AgentContext
from api.schemas.automations import (
    ConditionSpec,
    MAX_CHECK_MINUTES,
    MIN_CHECK_MINUTES,
)

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
        default=9,
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
    schedule_mode: Literal["fixed_time", "interval"] | None = Field(
        default=None,
        description=(
            "fixed_time when the user named a clock time; interval when they "
            "named a cadence. For an alert with no schedule, use interval."
        ),
    )
    kind: Literal["scheduled_report", "condition_alert"] = Field(
        default="scheduled_report",
        description=(
            "condition_alert for notify/alarm requests against live numeric values; "
            "scheduled_report for an answer produced at a clock time."
        ),
    )
    condition: ConditionSpec | None = Field(
        default=None,
        description="Required for condition_alert. Never invent missing inputs.",
    )
    interval_minutes: int | None = Field(
        default=None,
        ge=MIN_CHECK_MINUTES,
        le=MAX_CHECK_MINUTES,
        description="Frequency for any report or alert that runs every N minutes.",
    )
    window_start_minute: int | None = Field(
        default=None, ge=0, le=1439,
        description="Optional active-window start as minutes after midnight.",
    )
    window_end_minute: int | None = Field(
        default=None, ge=0, le=1439,
        description="Optional active-window end as minutes after midnight.",
    )


class UpdateAutomationInput(BaseModel):
    """One automation, named by its title, and the fields to change.

    **Named by title, not by id.** The rows have UUID primary keys and a model
    copying a UUID out of a previous tool result is a whole class of failure for
    nothing gained -- it cannot verify a UUID, so a single wrong character edits
    somebody's other automation or, more often, nothing at all. A title is
    something the model already knows: it either just wrote it or read it out of
    `list_automations`. When a title is ambiguous the tool refuses and says which
    ones matched, which is a conversation the model can have.
    """

    title: str = Field(
        description=(
            "The current title of the automation to change, as it appears in "
            "list_automations. Call that tool first if you are not sure. A "
            "distinctive part of the title is enough."
        )
    )
    new_title: str | None = Field(
        default=None,
        description=(
            "Rename it. Only when the user asked for a different name, or when "
            "the subject changed enough that the old name now describes the "
            "wrong thing."
        ),
    )
    prompt: str | None = Field(
        default=None,
        description=(
            "Replace the question it asks, written to stand alone with no memory "
            "of this conversation. This REPLACES the old question rather than "
            "adding to it, so restate everything that should stay -- the banks, "
            "products, currencies and the shape of the answer."
        ),
    )
    hour: int | None = Field(
        default=None, ge=0, le=23, description="New hour, 0-23, Turkey local time."
    )
    minute: int | None = Field(default=None, ge=0, le=59, description="New minute, 0-59.")
    weekdays: list[int] | None = Field(
        default=None,
        description=(
            "New days: 0=Monday ... 6=Sunday. An EMPTY list means every day. "
            "Omit the field entirely to leave the days alone -- empty and absent "
            "mean different things here."
        ),
    )
    enabled: bool | None = Field(
        default=None,
        description=(
            "false pauses it: it keeps its schedule and its reports but stops "
            "running. Use this for 'durdur', 'iptal et', 'artık istemiyorum' -- "
            "you cannot delete, and pausing is undoable where deleting is not. "
            "true resumes a paused one."
        ),
    )
    web_search: bool | None = Field(
        default=None, description="Allow or forbid live internet research on each run."
    )
    schedule_mode: Literal["fixed_time", "interval"] | None = Field(
        default=None,
        description=(
            "Set fixed_time to replace an interval with hour/minute/weekdays, "
            "or interval to replace a clock schedule with interval_minutes."
        ),
    )
    interval_minutes: int | None = Field(
        default=None,
        ge=MIN_CHECK_MINUTES,
        le=MAX_CHECK_MINUTES,
        description="New interval frequency for any automation.",
    )
    window_start_minute: int | None = Field(default=None, ge=0, le=1439)
    window_end_minute: int | None = Field(default=None, ge=0, le=1439)


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


def _local(moment) -> str:
    """A stored UTC instant as Turkish wall clock, for the model to read out.

    `astimezone(TZ)` and not `astimezone()`: the second one uses the *server's*
    zone, which is only right by accident on a laptop in Istanbul and silently
    wrong on any deployed machine. The rest of the app learned this the loud way
    -- see `banks/clock.py::stamp_tr` and `agents/shared/clock.py`.
    """
    if moment is None:
        return "bilinmiyor"
    return moment.astimezone(TZ).strftime("%d.%m.%Y %H:%M")


def _match_one(rows, wanted: str):
    """Find the one automation the model meant, or explain why it cannot.

    Returns `(row, None)` on a unique match and `(None, refusal)` otherwise, so
    the caller never has to distinguish "no match" from "several".

    Exact title first, then a unique case-insensitive substring. Both directions
    of substring are tried: the model may name a longer thing than the row is
    called ("Sabah altın raporu otomasyonu") or a shorter one ("altın").

    **Ambiguity is a refusal, never a guess.** Two automations about gold prices
    differ only in their wording, and editing the wrong one is a silent change to
    something the user did not mention. The refusal lists the candidates, which
    turns a wrong guess into a question.
    """
    if not rows:
        return None, (
            "Kullanıcının hiç otomasyonu yok, dolayısıyla değiştirecek bir şey "
            "de yok. Yeni bir tane kurmak isteyip istemediğini sor."
        )

    folded = wanted.casefold()
    exact = [r for r in rows if r.title.strip().casefold() == folded]
    if len(exact) == 1:
        return exact[0], None
    candidates = exact or [
        r
        for r in rows
        if folded in r.title.casefold() or r.title.casefold() in folded
    ]
    if len(candidates) == 1:
        return candidates[0], None

    titles = ", ".join(repr(r.title) for r in (candidates or rows))
    if not candidates:
        return None, (
            f"{wanted!r} adlı bir otomasyon bulamadım. Mevcut olanlar: {titles}. "
            "Kullanıcıya hangisini kastettiğini sor; tahmin etme."
        )
    return None, (
        f"{wanted!r} birden fazla otomasyona uyuyor: {titles}. Kullanıcıya "
        "hangisini kastettiğini sor; yanlış olanı değiştirme."
    )


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
        runtime: ToolRuntime[AgentContext],
        hour: int = 9,
        minute: int = 0,
        weekdays: list[int] | None = None,
        web_search: bool = True,
        schedule_mode: str | None = None,
        kind: str = "scheduled_report",
        condition: dict | ConditionSpec | None = None,
        interval_minutes: int | None = None,
        window_start_minute: int | None = None,
        window_end_minute: int | None = None,
    ) -> str:
        from api.automations.schedule import next_interval_run, next_run, valid_weekdays
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
        if kind == "condition_alert":
            if condition is None:
                return (
                    "Alarm kurulamadı: karşılaştırılacak canlı değerler eksik. "
                    "Eksik banka, tutar, vade, metrik, yön veya eşik değerini "
                    "kullanıcıya sor; tahmin etme."
                )
            try:
                condition_spec = ConditionSpec.model_validate(condition)
            except ValueError as exc:
                return f"Alarm kurulamadı: koşul geçersiz ({exc}). Eksik bilgiyi kullanıcıya sor."
            if schedule_mode != "fixed_time":
                interval_minutes = interval_minutes or 60
        else:
            condition_spec = None
        if (window_start_minute is None) != (window_end_minute is None):
            return "Zaman aralığı için hem başlangıç hem bitiş saati gereklidir."
        if window_start_minute is not None and interval_minutes is None:
            return "Zaman aralığı yalnızca aralıklı bir otomasyonda kullanılabilir."

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
                now = utcnow()
                row = Automation(
                    user_id=user_id,
                    title=clean_title,
                    prompt=clean_prompt,
                    hour=hour,
                    minute=minute,
                    weekdays=days,
                    web_search=bool(web_search),
                    kind=kind,
                    condition=(
                        condition_spec.model_dump(mode="json") if condition_spec else {}
                    ),
                    interval_minutes=interval_minutes,
                    window_start_minute=window_start_minute,
                    window_end_minute=window_end_minute,
                    enabled=True,
                    next_run_at=(
                        next_interval_run(
                            now,
                            interval_minutes,
                            days,
                            window_start_minute,
                            window_end_minute,
                            include_now=kind == "condition_alert",
                        )
                        if interval_minutes is not None
                        else next_run(now, hour, minute, days)
                    ),
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
            (
                f"every {interval_minutes} minutes"
                if interval_minutes is not None
                else _describe(hour, minute, days)
            ),
            first.isoformat(),
        )
        if interval_minutes is not None:
            return (
                f"Otomasyon kuruldu: {clean_title!r}, her {interval_minutes} dakikada "
                "çalışacak. Sonuçlar Profil → Raporlar altında birikecek ve bildirim "
                "zili kullanıcıyı uyaracak. Kullanıcıya çalışma sıklığını söyle."
            )
        return (
            f"Otomasyon kuruldu: {clean_title!r}, {_describe(hour, minute, days)}. "
            f"İlk çalışma: {first.astimezone().strftime('%d.%m.%Y %H:%M')}. "
            "Kullanıcıya kurulduğunu ve hangi saatte çalışacağını söyle. "
            "Raporlar 'Profil → Raporlar' sayfasında birikir; otomasyon listesi "
            "'Profil → Genel' sayfasındadır -- bunlar FARKLI sayfalar, "
            "karıştırma. Bildirim zili okunmamış raporu gösterir. Saati yanlış "
            "anlamış olabileceğini belirt ve yanlışsa DÜZELTEBİLECEĞİNİ söyle: "
            "update_automation ile saati, günleri ve başlığı değiştirebilirsin, "
            "onu sayfaya yönlendirmen gerekmez."
        )

    def update_automation(
        title: str,
        runtime: ToolRuntime[AgentContext],
        new_title: str | None = None,
        prompt: str | None = None,
        hour: int | None = None,
        minute: int | None = None,
        weekdays: list[int] | None = None,
        enabled: bool | None = None,
        web_search: bool | None = None,
        schedule_mode: str | None = None,
        interval_minutes: int | None = None,
        window_start_minute: int | None = None,
        window_end_minute: int | None = None,
    ) -> str:
        from api.automations.schedule import next_interval_run, next_run, valid_weekdays
        from api.db.base import utcnow
        from api.db.models import Automation
        from api.db.session import session_scope

        user_id = _user_id(runtime)
        if user_id is None:
            return (
                "Bu oturumda otomasyon değiştiremiyorum çünkü hesap bilgisi yok. "
                "Kullanıcıya giriş yapıp Profil sayfasından düzenlemesini söyle."
            )

        wanted = (title or "").strip()
        if not wanted:
            return (
                "Hangi otomasyonu değiştireceğimi bilmiyorum. Önce "
                "list_automations ile listeyi oku, sonra başlığı ver."
            )

        # `weekdays` is the one field where absent and empty differ, so the check
        # is on the argument being `None` rather than on it being falsy: `[]` is
        # the user asking for every day.
        changes = {
            "title": (new_title or "").strip()[:TITLE_CHARS] or None,
            "prompt": (prompt or "").strip() or None,
            "hour": hour,
            "minute": minute,
            "weekdays": None if weekdays is None else valid_weekdays(weekdays),
            "enabled": enabled,
            "web_search": web_search,
            "interval_minutes": interval_minutes,
            "window_start_minute": window_start_minute,
            "window_end_minute": window_end_minute,
        }
        if all(value is None for value in changes.values()) and schedule_mode is None:
            return (
                "Değiştirilecek bir alan verilmedi. Kullanıcıya neyi "
                "değiştirmek istediğini sor (saat, günler, başlık, durdur/başlat)."
            )

        try:
            with session_scope() as store:
                rows = store.scalars(
                    select(Automation)
                    .where(Automation.user_id == user_id)
                    .order_by(Automation.created_at)
                ).all()
                match, problem = _match_one(rows, wanted)
                if problem is not None:
                    return problem

                match_kind = getattr(match, "kind", "scheduled_report")
                for field, value in changes.items():
                    if value is not None:
                        setattr(match, field, value)
                if schedule_mode == "fixed_time":
                    match.interval_minutes = None
                    match.window_start_minute = None
                    match.window_end_minute = None
                elif schedule_mode == "interval":
                    if interval_minutes is None and match.interval_minutes is None:
                        return (
                            "Aralıklı programa geçmek için interval_minutes gerekli. "
                            "Kullanıcıdan çalışma sıklığını sor; tahmin etme."
                        )
                if (
                    getattr(match, "window_start_minute", None) is None
                ) != (getattr(match, "window_end_minute", None) is None):
                    return "Zaman aralığı için hem başlangıç hem bitiş saati gereklidir."
                # Recomputed from *now*, exactly as `PATCH /me/automations/{id}`
                # does. Keeping the old value would fire the automation once more
                # at the time the user just changed away from, which is precisely
                # the mistake they were correcting.
                schedule_moved = any(
                    changes[field] is not None
                    for field in ("hour", "minute", "weekdays", "enabled")
                )
                schedule_moved = schedule_moved or any(
                    value is not None
                    for value in (interval_minutes, window_start_minute, window_end_minute)
                )
                schedule_moved = schedule_moved or schedule_mode is not None
                if schedule_moved:
                    now = utcnow()
                    match.next_run_at = (
                        next_interval_run(
                            now,
                            match.interval_minutes,
                            match.weekdays,
                            getattr(match, "window_start_minute", None),
                            getattr(match, "window_end_minute", None),
                            include_now=match_kind == "condition_alert",
                        )
                        if match.interval_minutes is not None
                        else next_run(now, match.hour, match.minute, match.weekdays)
                    )
                store.flush()
                changed_id = match.id
                final_title = match.title
                schedule = (
                    f"her {match.interval_minutes} dakikada"
                    if match.interval_minutes is not None
                    else _describe(match.hour, match.minute, match.weekdays)
                )
                paused = not match.enabled
                first = match.next_run_at
        except Exception as exc:  # noqa: BLE001 - a traceback must not reach the model
            logger.exception("update_automation failed user=%s", user_id)
            return (
                f"Otomasyon güncellenemedi ({type(exc).__name__}). Kullanıcıya "
                "değiştirilemediğini söyle; aynı bilgilerle tekrar DENEME."
            )

        logger.info(
            "automation updated id=%s user=%s title=%r schedule=%s paused=%s next=%s",
            changed_id, user_id, final_title, schedule, paused,
            first.isoformat() if first else None,
        )
        if paused:
            return (
                f"Otomasyon durduruldu: {final_title!r}. Silinmedi -- geçmiş "
                "raporları duruyor ve istediğinde yeniden başlatılabilir. "
                "Kullanıcıya durdurulduğunu ve tekrar açılabileceğini söyle."
            )
        return (
            f"Otomasyon güncellendi: {final_title!r}, artık {schedule}. "
            f"Sıradaki çalışma: {_local(first)}. "
            "Kullanıcıya neyi değiştirdiğini ve yeni saati söyle."
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
                    f"{i}. {r.title} -- "
                    + (
                        f"her {r.interval_minutes} dakikada"
                        if getattr(r, "interval_minutes", None) is not None
                        else _describe(r.hour, r.minute, r.weekdays)
                    )
                    + ("" if r.enabled else "  (DURDURULMUŞ)")
                    for i, r in enumerate(rows, 1)
                ]
        except Exception as exc:  # noqa: BLE001
            logger.exception("list_automations failed user=%s", user_id)
            return f"Otomasyon listesi okunamadı ({type(exc).__name__})."
        if not lines:
            return "Kullanıcının kurulu otomasyonu yok."
        return "\n".join(lines) + (
            "\n\nSaatini, günlerini veya başlığını update_automation ile "
            "değiştirebilir, enabled=false ile durdurabilirsin -- başlığıyla "
            "seç. SİLME yetkin yok: kalıcı silme 'Profil → Genel' sayfasındaki "
            "satırdan yapılır. Kullanıcı silmek isterse durdurmayı öner."
        )

    return [
        StructuredTool.from_function(
            func=create_automation,
            name="create_automation",
            description=(
                "Set up either a recurring report/research task or a conditional "
                "live-value alert. Scheduling is independent of kind: both may run "
                "at a fixed clock time or every interval_minutes, optionally only "
                "on selected weekdays and inside a daily time window. An alert checks "
                "a validated numeric condition and notifies "
                "only when false becomes true. Never invent an alert's missing "
                "bank, amount, term, metric, buy/sell side, currency or threshold; "
                "ask the user first. Results are saved to Reports and announced by the "
                "notification bell. Use this when the user asks for something "
                "repeating or says 'alarm ver / haber ver / notify me when'. "
                "Do NOT use it for a question they want "
                "answered now; answer that normally. Storing the request is all "
                "this does -- it retrieves nothing and proves nothing, so still "
                "answer today's version of the question from the bank "
                "specialists if they asked for one."
            ),
            args_schema=CreateAutomationInput,
        ),
        StructuredTool.from_function(
            func=update_automation,
            name="update_automation",
            description=(
                "Change an automation the user already has: its fixed time or "
                "interval, optional time window, days, title, question, or whether it runs at "
                "all. Identify it by its current title. Use this when they "
                "correct you ('hayır, 19:00 olsun'), change their mind about "
                "when or what ('pazartesileri de olsun', 'dolar kurunu da "
                "ekle'), or want one stopped -- set enabled=false to pause, "
                "which is reversible. You cannot delete; pausing is the closest "
                "thing and loses nothing. Call list_automations first if you are "
                "not certain which one they mean."
            ),
            args_schema=UpdateAutomationInput,
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
