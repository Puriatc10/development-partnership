"""Turn the pages we actually read into admissible findings.

The engine's promise is "we read the site and cited what we read". This module is where that
promise is kept or broken. Every finding is built through :func:`make_finding`, which refuses an
excerpt that is not verbatim in the page it cites, so there is no code path that can add a claim
without a quote behind it. A misfiring pattern therefore degrades to *no finding*, never to an
invented one — which is the only failure mode that matters here.

Confidence is a rule, not a mood:

    high        a name, a number or a figure the page states in its own words
    medium      a descriptive statement (a service, a process, a technology mention)
    low         an inference distilled from the page's language (growth, positioning)
    unverified  an information gap: we counted an absence, we did not observe a fact

Gaps are findings too, and they are the honest ones. When no page states a price, a headcount or
who owns the backend, the payload says so instead of leaving a hole that a later step would fill
by guessing. Each gap cites the page it was measured against and quotes that page's opening
words, because "the site says nothing about pricing" is only meaningful relative to a corpus
someone actually read.

Categories and finding types are imported from the IPP vocabulary rather than copied, so a typo
here would be caught by :func:`make_finding` before a database constraint has to catch it.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import cast
from urllib.parse import urlsplit

from intelligence.ipp.models import FINDING_CATEGORIES, FINDING_TYPES
from research.research_types import (
    Confidence,
    DraftFinding,
    PageContent,
    excerpt_is_grounded,
    normalise,
)

#: Mirrors the shared grounding rule (``excerpt_is_grounded``): shorter quotes match too easily to
#: prove anything.
MIN_EXCERPT_CHARS = 12

#: Total ceiling. A report is a briefing, not a transcript: past this the executor's reviewers stop
#: reading, and a long list would also drown the gaps that deserve attention.
MAX_FINDINGS = 40

#: Per-kind ceiling, applied while collecting so one chatty page cannot monopolise the report.
KIND_CAPS: dict[str, int] = {
    "service": 6,
    "client": 6,
    "technology": 8,
    "team": 4,
    "pricing": 3,
    "growth": 3,
    "process": 4,
    "contact": 4,
    "gap": 3,
}
_DEFAULT_KIND_CAP = 4

#: ``confidence_level_type`` includes ``unverified``; the shared ``Confidence`` alias is narrower
#: because it describes claims, and a gap is explicitly not one.
UNVERIFIED: Confidence = cast(Confidence, "unverified")

#: Prefix shared by the team-size statement, so the gap logic can tell "headcount stated" from
#: "roles named" without re-parsing prose.
TEAM_SIZE_PREFIX = "States a team size of "

GAP_TYPE = "information_gap_tbd"
FACT_TYPE = "verified_fact"
HYPOTHESIS_TYPE = "hypothesis"

# -----------------------------------------------------------------------------
# Patterns. Deliberately narrow: a false positive costs more than a missed mention,
# because a false positive ends up as a citable claim in a report.
# -----------------------------------------------------------------------------

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")

#: Past-tense delivery language: a case study or testimonial, not an offering statement.
_PORTFOLIO_CUE = re.compile(
    r"\bwe\s+(?:designed|developed|built|delivered|launched|created|shipped|redesigned|helped"
    r"|partnered with|worked with)\b"
    r"|\bthe team\s+(?:provided|delivered|built|designed|developed)\b",
    re.IGNORECASE,
)

#: "… for Xero" inside a delivery sentence: the client a case study names.
_PORTFOLIO_CLIENT = re.compile(r"\bfor\s+([A-Z][A-Za-z0-9&.'\-]*(?:\s+[A-Z][A-Za-z0-9&.'\-]*){0,2})")

#: Contact-page furniture that reads like a channel but tells a reader nothing.
_CONTACT_NOISE = re.compile(
    r"\b(?:send again|resend|cookie|newsletter|subscribe|sign up|log in|privacy policy)\b",
    re.IGNORECASE,
)

_SERVICE_CUE = re.compile(
    r"\b(?:we|our team|the team)\s+"
    r"(?:build|design|develop|create|deliver|offer|provide|speciali[sz]e)"
    r"|\bour services\b|\bwhat we do\b|\bservices include\b|\bwe help\b|\bwe partner\b",
    re.IGNORECASE,
)

_PROCESS_CUE = re.compile(
    r"\b(?:discovery|scoping|scope|sprints?|milestones?|kick-?off|statement of work|sow|agile"
    r"|fixed[- ]scope|change requests?|estimates?|proposals?|wireframes?|design systems?"
    r"|roadmaps?|retrospectives?|qa|quality assurance)\b",
    re.IGNORECASE,
)

_CLIENT_TRIGGER = re.compile(
    r"\b(?:trusted by|clients include|our clients|worked with|working with|clients?"
    r"|selected by|brands like|partners include|testimonial from)\s*[:\-]?\s*",
    re.IGNORECASE,
)

#: Words that make a candidate "name" a sentence fragment rather than a company.
_NOT_A_NAME: frozenset[str] = frozenset(
    {
        "we", "our", "the", "a", "an", "and", "them", "you", "your", "us", "their", "clients",
        "client", "brands", "brand", "companies", "company", "businesses", "startups", "startup",
        "teams", "team", "people", "leaders", "organisations", "organizations", "agencies",
        "agency", "studios", "studio", "founders", "enterprises", "firms", "everyone", "many",
        "some", "more", "leading", "global", "worldwide", "industry", "industries",
    }
)

#: Hero/benefit copy read as a client list. A real agency page says "trusted by Acme"; a marketing
#: page says "trusted by teams who need Design Assets Brand Templates faster". Words here make a
#: candidate a phrase, not a company, and precision matters more than recall.
_GENERIC_NAME_WORDS: frozenset[str] = frozenset(
    {
        "assets", "templates", "template", "brand", "brands", "branding", "design", "designs",
        "marketing", "launch", "launches", "impact", "faster", "lower", "cost", "costs",
        "consistent", "content", "strategy", "growth", "product", "products", "digital", "web",
        "website", "websites", "app", "apps", "experience", "experiences", "solution", "solutions",
        "service", "services", "results", "quality", "scale", "speed", "teams", "your",
    }
)

#: A trailing clause means we captured more than the name.
_NOT_NAME_WORDS: frozenset[str] = frozenset(
    {
        "since", "to", "for", "with", "who", "that", "which", "and", "in", "on", "by", "from",
        "our", "we", "is", "are", "was", "were", "has", "have", "including", "among", "across",
    }
)

#: (display name, tokens). Bare "sanity" is excluded on purpose: "sanity check" is ordinary copy.
_TECHNOLOGY_SPECS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Webflow", ("webflow",)),
    ("Framer", ("framer",)),
    ("Shopify", ("shopify",)),
    ("React", ("react", "react.js", "reactjs")),
    ("Next.js", ("next.js", "nextjs")),
    ("HubSpot", ("hubspot",)),
    ("WordPress", ("wordpress",)),
    ("Sanity", ("sanity.io", "sanity studio", "sanity cms")),
    ("Contentful", ("contentful",)),
    ("Supabase", ("supabase",)),
    ("Firebase", ("firebase",)),
    ("Stripe", ("stripe",)),
    ("Vue", ("vue.js", "vuejs")),
    ("Nuxt", ("nuxt",)),
    ("Gatsby", ("gatsby",)),
    ("Prismic", ("prismic",)),
    ("Airtable", ("airtable",)),
    ("Zapier", ("zapier",)),
    ("Algolia", ("algolia",)),
    ("Auth0", ("auth0",)),
    ("Memberstack", ("memberstack",)),
    ("Outseta", ("outseta",)),
)


def _token_pattern(token: str) -> re.Pattern[str]:
    """Word-boundary match that tolerates a trailing domain dot but not ``preact``/``reacts``."""
    return re.compile(rf"(?<![\w.]){re.escape(token)}(?![\w])", re.IGNORECASE)


_TECHNOLOGIES: tuple[tuple[str, tuple[re.Pattern[str], ...]], ...] = tuple(
    (display, tuple(_token_pattern(token) for token in tokens))
    for display, tokens in _TECHNOLOGY_SPECS
)

_STACK_CUE = re.compile(
    r"\b(?:built|build|building|powered by|developed (?:with|in)|our stack|tech stack"
    r"|we use|we're using|we are using|using)\b",
    re.IGNORECASE,
)

_TEAM_OF = re.compile(
    r"\bteam of\s+(?:about\s+|around\s+|over\s+|nearly\s+)?(\d{1,4})\+?\b",
    re.IGNORECASE,
)
#: Anchored to a headcount context on purpose: "we are hiring 2 engineers" is a growth signal,
#: not a team of two. A bare "N engineers" would make every job ad a headcount claim.
_TEAM_HEADCOUNT = re.compile(
    r"\b(?:we(?:'re| are)?|our team (?:is|has)|the team (?:is|has)|currently)\s+"
    r"(?:about\s+|around\s+|over\s+|nearly\s+)?(\d{1,4})\+?\s*"
    r"(?:people|employees|staff|engineers|developers|designers|specialists|strategists"
    r"|team members|colleagues)\b",
    re.IGNORECASE,
)
_TEAM_HYPHEN = re.compile(r"\b(\d{1,4})[-\s]person (?:team|studio|agency|company)\b", re.IGNORECASE)
_MAX_PLAUSIBLE_HEADCOUNT = 5000

_ROLE_TITLES: tuple[str, ...] = (
    "founder", "co-founder", "ceo", "cto", "coo", "managing director", "creative director",
    "design director", "technical director", "head of engineering", "head of design",
    "project manager", "account manager", "producer", "principal", "partner", "lead developer",
    "lead designer", "strategist",
)

_PRICE_FIGURE = re.compile(
    r"(?:[$£€]\s?\d[\d,]*(?:\.\d+)?"
    r"|\b\d[\d,]*\s?(?:usd|eur|gbp|dollars|pounds|euros)\b"
    r"|\b(?:usd|eur|gbp)\s?\d[\d,]*(?:\.\d+)?)",
    re.IGNORECASE,
)
_PRICING_LANGUAGE = re.compile(
    r"\b(?:pricing|price list|prices?|rates?|retainers?|day rate|hourly rate|per month|monthly"
    r"|fixed[- ]price|starting at)\b",
    re.IGNORECASE,
)

_GROWTH_CUE = re.compile(
    r"\b(?:we(?:'re| are) hiring|now hiring|join (?:our|the) team|open roles?|growing team"
    r"|we(?:'ve| have) grown|expanding|new (?:office|studio)|scaling)\b",
    re.IGNORECASE,
)

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE = re.compile(r"(?<![\w])(?:\+\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?){2,4}\d{2,4}(?![\w])")
_CONTACT_CUE = re.compile(
    r"\b(?:book a call|schedule a call|get in touch|contact form|drop us a line|say hello"
    r"|start a project|request a quote)\b",
    re.IGNORECASE,
)

_BACKEND_CUE = re.compile(
    r"\b(?:back-?end|api|database|server|infrastructure|devops|node\.?js|python|postgres"
    r"|supabase|hosting|cloud functions?)\b",
    re.IGNORECASE,
)


# -----------------------------------------------------------------------------
# Grounded construction: the single door every finding goes through
# -----------------------------------------------------------------------------


def make_finding(
    statement: str,
    excerpt: str,
    page: PageContent,
    *,
    category: str,
    finding_type: str,
    confidence: Confidence,
    kind: str,
) -> DraftFinding | None:
    """Build one finding, or return None when its excerpt is not verbatim in ``page``.

    Returning None instead of raising is deliberate: extractors run per page and per sentence, and
    a pattern that matched across a boundary should cost that one candidate, not the corpus. It
    also means the caller cannot "handle" an ungrounded finding by keeping it.
    """
    clean_statement = normalise(statement)
    clean_excerpt = normalise(excerpt)
    if not clean_statement or len(clean_excerpt) < MIN_EXCERPT_CHARS:
        return None
    if category not in FINDING_CATEGORIES:
        raise ValueError(
            f"category {category!r} is not a deployed finding_category_type; "
            f"allowed: {', '.join(FINDING_CATEGORIES)}"
        )
    if finding_type not in FINDING_TYPES:
        raise ValueError(
            f"finding_type {finding_type!r} is not a deployed research_finding_type; "
            f"allowed: {', '.join(FINDING_TYPES)}"
        )
    if not excerpt_is_grounded(clean_excerpt, page.text):
        return None
    return DraftFinding(
        statement=clean_statement,
        excerpt=clean_excerpt,
        source_url=page.url,
        category=category,
        finding_type=finding_type,
        confidence=confidence,
        kind=kind,
    )


# -----------------------------------------------------------------------------
# Text helpers
# -----------------------------------------------------------------------------


def page_sentences(text: str) -> tuple[str, ...]:
    """Sentences of the normalised text, so any excerpt cut from one is verbatim by construction."""
    return tuple(sentence for sentence in _SENTENCE_SPLIT.split(normalise(text)) if sentence)


def clip(text: str, limit: int = 200) -> str:
    """Trim prose to a readable statement. Only used for statements, never for excerpts."""
    cleaned = normalise(text)
    if len(cleaned) <= limit:
        return cleaned
    cut = cleaned[:limit]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:.-")


def excerpt_around(text: str, needle: str, *, span: int = 70) -> str:
    """A verbatim window around ``needle``, for matches a sentence split would cut in half."""
    haystack = normalise(text)
    index = haystack.lower().find(normalise(needle).lower())
    if index < 0:
        return ""
    start = max(0, index - span)
    end = min(len(haystack), index + len(needle) + span)
    if start > 0:
        space = haystack.find(" ", start)
        start = space + 1 if space != -1 else start
    if end < len(haystack):
        space = haystack.rfind(" ", start, end)
        end = space if space > index else end
    return haystack[start:end].strip(" ,;")


def opening_excerpt(page: PageContent, limit: int = 180) -> str:
    """The page's opening words, verbatim.

    Used to cite the corpus a gap was measured against: the statement asserts the absence, the
    excerpt proves we read the page we are talking about.
    """
    text = normalise(page.text)
    if len(text) < MIN_EXCERPT_CHARS:
        return ""
    cut = text[:limit]
    if len(text) > limit and " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut if len(cut) >= MIN_EXCERPT_CHARS else ""


# -----------------------------------------------------------------------------
# Extractors, one per kind
# -----------------------------------------------------------------------------


def _service_findings(page: PageContent) -> list[DraftFinding]:
    """Sentences where the page describes what it sells.

    A sentence that also talks about process is scoping evidence (how they work) rather than
    business-model evidence (what they sell), so the category follows the stronger cue.
    """
    out: list[DraftFinding] = []
    for sentence in page_sentences(page.text):
        if not _SERVICE_CUE.search(sentence):
            continue
        if _PORTFOLIO_CUE.search(sentence):
            # "We delivered X for Y" is a case study: it names a client, it does not describe
            # what the studio sells. Recording it as a service offering puts a past project into
            # the briefing as a capability.
            continue
        category = "scoping_maturity" if _PROCESS_CUE.search(sentence) else "business_model"
        finding = make_finding(
            f"States a service offering: {clip(sentence, 180)}",
            sentence,
            page,
            category=category,
            finding_type=FACT_TYPE,
            confidence="medium",
            kind="service",
        )
        if finding:
            out.append(finding)
    return out


def _looks_like_name(candidate: str) -> bool:
    """A conservative proper-noun test: precision matters more than recall here.

    A missed client costs one finding; an invented client ends up in a briefing as a fact about
    the prospect's roster, which is exactly the failure this engine exists to avoid.
    """
    name = candidate.strip().strip(".:;,-—\"'()").strip()
    if not (2 <= len(name) <= 40):
        return False
    words = name.split()
    if not (1 <= len(words) <= 4):
        return False
    if "@" in name or any(character.isdigit() for character in name):
        return False
    first = words[0]
    if not first[:1].isupper():
        return False
    if first.lower() in _NOT_A_NAME:
        return False
    if any(word.lower() in _GENERIC_NAME_WORDS for word in words):
        return False
    return not any(word.lower() in _NOT_NAME_WORDS for word in words[1:])


def _client_findings(page: PageContent) -> list[DraftFinding]:
    """Named clients, taken only from a trigger phrase ("trusted by …", "our clients include …")."""
    out: list[DraftFinding] = []
    for sentence in page_sentences(page.text):
        trigger = _CLIENT_TRIGGER.search(sentence)
        if trigger is None:
            continue
        # "our clients include Acme" leaves "include Acme" behind the trigger, so drop the
        # connector before splitting on the list separators.
        tail = re.sub(
            r"^(?:(?:include|includes|including|are|is|such as|like|namely)\b[\s:,-]*)+",
            "",
            sentence[trigger.end() :].strip(),
            flags=re.IGNORECASE,
        )
        names: list[str] = []
        for part in re.split(r",|;|\band\b|&|\|", tail):
            candidate = part.strip()
            candidate = re.sub(r"^(?:the|our)\s+", "", candidate, flags=re.IGNORECASE)
            if _looks_like_name(candidate):
                names.append(candidate.strip().strip(".:;,-—\"'()").strip())
        for name in names[:6]:
            finding = make_finding(
                f"Names {name} as a client",
                sentence,
                page,
                category="client_roster",
                finding_type=FACT_TYPE,
                confidence="high",
                kind="client",
            )
            if finding:
                out.append(finding)
    return out


def _portfolio_findings(page: PageContent) -> list[DraftFinding]:
    """Clients named by a case study ("We delivered rapid UI prototyping for Xero").

    This is the honest reading of a portfolio sentence: the named company is evidence of a client
    relationship, and the sentence itself is quotable. It is not evidence of what the studio sells
    today, which is why the service extractor skips the same sentences.
    """
    out: list[DraftFinding] = []
    for sentence in page_sentences(page.text):
        if not _PORTFOLIO_CUE.search(sentence):
            continue
        for match in _PORTFOLIO_CLIENT.finditer(sentence):
            name = match.group(1).strip().strip(".,;:")
            if not _looks_like_name(name):
                continue
            finding = make_finding(
                f"Delivered work for {name}",
                sentence,
                page,
                category="client_roster",
                finding_type=FACT_TYPE,
                confidence="high",
                kind="portfolio",
            )
            if finding:
                out.append(finding)
    return out


def _technology_findings(page: PageContent) -> list[DraftFinding]:
    """Technology mentions. High only where the sentence also claims use ("built with"/"we use")."""
    out: list[DraftFinding] = []
    claimed: set[str] = set()
    for sentence in page_sentences(page.text):
        for display, patterns in _TECHNOLOGIES:
            if display in claimed:
                continue
            if not any(pattern.search(sentence) for pattern in patterns):
                continue
            confidence: Confidence = "high" if _STACK_CUE.search(sentence) else "medium"
            finding = make_finding(
                f"Uses or mentions {display}",
                sentence,
                page,
                category="technology_stack",
                finding_type=FACT_TYPE,
                confidence=confidence,
                kind="technology",
            )
            if finding:
                out.append(finding)
                claimed.add(display)
    return out


#: Acronyms that ``str.title()`` mangles into "Ceo" and "Cto".
_ROLE_ACRONYMS = frozenset({"ceo", "cto", "cfo", "coo", "cmo", "cpo", "vp", "svp", "evp", "coo", "hr"})


def _display_role(role: str) -> str:
    return " ".join(
        word.upper() if word.lower().strip(".,") in _ROLE_ACRONYMS else word.capitalize()
        for word in role.split()
    )


def _team_findings(page: PageContent) -> list[DraftFinding]:
    """Headcount (only with a number) and role titles."""
    out: list[DraftFinding] = []
    for sentence in page_sentences(page.text):
        size = (
            _TEAM_OF.search(sentence)
            or _TEAM_HEADCOUNT.search(sentence)
            or _TEAM_HYPHEN.search(sentence)
        )
        if size is not None:
            number = int(size.group(1))
            if 1 <= number <= _MAX_PLAUSIBLE_HEADCOUNT:
                finding = make_finding(
                    f"{TEAM_SIZE_PREFIX}{number} people",
                    sentence,
                    page,
                    category="team_structure",
                    finding_type=FACT_TYPE,
                    confidence="high",
                    kind="team",
                )
                if finding:
                    out.append(finding)
        for role in _ROLE_TITLES:
            if not re.search(rf"\b{re.escape(role)}\b", sentence, re.IGNORECASE):
                continue
            finding = make_finding(
                f"Names the role {_display_role(role)}",
                sentence,
                page,
                category="team_structure",
                finding_type=FACT_TYPE,
                confidence="medium",
                kind="team",
            )
            if finding:
                out.append(finding)
    return out


def _pricing_findings(page: PageContent) -> list[DraftFinding]:
    """Published figures (high) and pricing/retainer language without a figure (medium)."""
    out: list[DraftFinding] = []
    for sentence in page_sentences(page.text):
        if _PRICE_FIGURE.search(sentence):
            finding = make_finding(
                f"Publishes a price: {clip(sentence, 160)}",
                sentence,
                page,
                category="pricing_rates",
                finding_type=FACT_TYPE,
                confidence="high",
                kind="pricing",
            )
        elif _PRICING_LANGUAGE.search(sentence):
            finding = make_finding(
                f"Speaks about pricing: {clip(sentence, 160)}",
                sentence,
                page,
                category="pricing_rates",
                finding_type=FACT_TYPE,
                confidence="medium",
                kind="pricing",
            )
        else:
            continue
        if finding:
            out.append(finding)
    return out


def _growth_findings(page: PageContent) -> list[DraftFinding]:
    """Hiring/expansion language: an observation with figures, an inference without them."""
    out: list[DraftFinding] = []
    for sentence in page_sentences(page.text):
        if not _GROWTH_CUE.search(sentence):
            continue
        if re.search(r"\b\d{1,4}\b", sentence):
            finding = make_finding(
                f"Reports hiring or expansion: {clip(sentence, 160)}",
                sentence,
                page,
                category="growth_signals",
                finding_type=FACT_TYPE,
                confidence="high",
                kind="growth",
            )
        else:
            finding = make_finding(
                f"Growth language, no figures: {clip(sentence, 160)}",
                sentence,
                page,
                category="growth_signals",
                finding_type=HYPOTHESIS_TYPE,
                confidence="low",
                kind="growth",
            )
        if finding:
            out.append(finding)
    return out


def _process_findings(page: PageContent) -> list[DraftFinding]:
    """How the studio says it runs an engagement: scoping maturity, stated not inferred."""
    out: list[DraftFinding] = []
    for sentence in page_sentences(page.text):
        if not _PROCESS_CUE.search(sentence):
            continue
        finding = make_finding(
            f"Describes its process: {clip(sentence, 160)}",
            sentence,
            page,
            category="scoping_maturity",
            finding_type=FACT_TYPE,
            confidence="medium",
            kind="process",
        )
        if finding:
            out.append(finding)
    return out


def _printed_number(text: str, digits: str) -> str | None:
    """The span of ``text`` that prints ``digits``, or None when no such span exists.

    A ``tel:`` link can name a number the page never prints. Quoting the link would be a citation
    that cannot be checked against the page, so such a number is dropped instead.
    """
    for candidate in re.finditer(r"\+?[\d(][\d\s().\-]{7,24}\d", normalise(text)):
        span = candidate.group(0).strip()
        if re.sub(r"\D", "", span) == digits:
            return span
    return None


def _contact_findings(page: PageContent) -> list[DraftFinding]:
    """Contact channels (business model: how work arrives) and decision-maker titles."""
    out: list[DraftFinding] = []
    emails = {email.strip() for email in page.emails if email.strip()}
    emails.update(_EMAIL.findall(page.text))
    for email in sorted(emails):
        excerpt = excerpt_around(page.text, email)
        finding = make_finding(
            f"Publishes a contact email: {email}",
            excerpt,
            page,
            category="business_model",
            finding_type=FACT_TYPE,
            confidence="high",
            kind="contact",
        )
        if finding:
            out.append(finding)

    numbers: list[str] = list(page.phones)
    for match in _PHONE.finditer(normalise(page.text)):
        number = match.group(0).strip()
        # A run of digit groups separated by spaces is usually an address glued to a number
        # ("… CA, 90034, United States +1 323 863 80 68 1178"). Only a string that is punctuated
        # like a phone number is accepted from prose; `tel:` links are trusted as-is.
        if not any(mark in number for mark in ("(", ")", "-")):
            continue
        numbers.append(number)

    for number in dict.fromkeys(numbers):
        # Some sites obfuscate `tel:` links ("+141****7880") and fill the gaps with JavaScript.
        # The visible digits cannot be read as a phone number, so the finding is dropped rather
        # than recorded with a mask in it.
        if re.search(r"[^\d+\s().\-]", number):
            continue
        digits = re.sub(r"\D", "", number)
        if not (9 <= len(digits) <= 15):
            continue
        # The excerpt has to be text that is really on the page, so a `tel:`-derived number is
        # quoted from the span that prints the same digits rather than from the link.
        printed = _printed_number(page.text, digits)
        if printed is None:
            continue
        finding = make_finding(
            f"Publishes a phone number: {number}",
            excerpt_around(page.text, printed),
            page,
            category="business_model",
            finding_type=FACT_TYPE,
            confidence="medium",  # a number-shaped string is weaker evidence than an address
            kind="contact",
        )
        if finding:
            out.append(finding)

    for sentence in page_sentences(page.text):
        if _CONTACT_CUE.search(sentence) and not _CONTACT_NOISE.search(sentence):
            finding = make_finding(
                f"Offers a contact route: {clip(sentence, 160)}",
                sentence,
                page,
                category="business_model",
                finding_type=FACT_TYPE,
                confidence="medium",
                kind="contact",
            )
            if finding:
                out.append(finding)
        for role in ("founder", "co-founder", "ceo", "cto", "managing director", "partner"):
            pattern = rf"\b{re.escape(role)}\b"
            if not re.search(pattern, sentence, re.IGNORECASE):
                continue
            finding = make_finding(
                f"Names a decision-maker title: {role.title()}",
                sentence,
                page,
                category="other",
                finding_type=FACT_TYPE,
                confidence="medium",
                kind="team",
            )
            if finding:
                out.append(finding)
    return out


_EXTRACTORS: tuple[Callable[[PageContent], list[DraftFinding]], ...] = (
    _service_findings,
    _process_findings,
    _client_findings,
    _portfolio_findings,
    _technology_findings,
    _team_findings,
    _pricing_findings,
    _growth_findings,
    _contact_findings,
)


# -----------------------------------------------------------------------------
# Information gaps
# -----------------------------------------------------------------------------


def _anchor_page(pages: Sequence[PageContent]) -> PageContent:
    """The homepage when we have it, else the first page: the corpus's front door."""
    for page in pages:
        if normalise(_path_only(page.url)) == "/":
            return page
    return pages[0]


def _path_only(url: str) -> str:
    return urlsplit(url).path or "/"


def find_information_gaps(
    pages: Sequence[PageContent], findings: Sequence[DraftFinding]
) -> list[DraftFinding]:
    """Emit one gap per unanswered question, each citing the corpus that was silent.

    The three questions are the ones a managed-development partner cannot price a conversation
    without: what it costs, who does the work, and who owns the backend. They are ``unverified``
    by construction — absence is counted, not observed.
    """
    corpus = [page for page in pages if normalise(page.text)]
    if not corpus:
        return []
    anchor = _anchor_page(corpus)
    excerpt = opening_excerpt(anchor)
    if not excerpt:
        return []

    count = len(corpus)
    categories = {finding.category for finding in findings}
    headcount_stated = any(finding.statement.startswith(TEAM_SIZE_PREFIX) for finding in findings)

    gaps: list[DraftFinding] = []

    def gap(statement: str, category: str) -> None:
        finding = make_finding(
            statement,
            excerpt,
            anchor,
            category=category,
            finding_type=GAP_TYPE,
            confidence=UNVERIFIED,
            kind="gap",
        )
        if finding:
            gaps.append(finding)

    if "pricing_rates" not in categories:
        gap(
            f"No published price, rate or retainer figure appears on the {count} page(s) read; "
            "pricing_rates stays unmeasured.",
            "pricing_rates",
        )
    if not headcount_stated:
        gap(
            f"No team size is stated on the {count} page(s) read; headcount stays unmeasured.",
            "team_structure",
        )
    if not any(_BACKEND_CUE.search(normalise(page.text)) for page in corpus):
        gap(
            f"No page states who owns the backend, database or hosting across the {count} page(s) "
            "read; engineering ownership is unverified.",
            "technology_stack",
        )
    return gaps


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------


def _cap_total(findings: list[DraftFinding]) -> list[DraftFinding]:
    """Trim to ``MAX_FINDINGS`` while keeping every gap: a gap is the cheapest useful sentence."""
    if len(findings) <= MAX_FINDINGS:
        return findings
    gaps = [finding for finding in findings if finding.finding_type == GAP_TYPE]
    others = [finding for finding in findings if finding.finding_type != GAP_TYPE]
    return others[: max(0, MAX_FINDINGS - len(gaps))] + gaps


def derive_findings(pages: Sequence[PageContent]) -> list[DraftFinding]:
    """Derive grounded findings from the pages we read, deterministically.

    Ordering is input order (pages), then extractor order (services, process, clients, technology,
    team, pricing, growth, contact), then appearance in the text; gaps come last in their fixed
    order. Dedupe is by normalised statement, first occurrence wins, so the same sentence quoted
    from two pages yields one finding rather than a fake corroboration count.

    Nothing here reaches the network or the database: the output is what the caller chooses to
    validate and persist, and every member is quotable back to the page in ``source_url``.
    """
    corpus: list[PageContent] = []
    seen_pages: set[str] = set()
    for page in pages:
        if page is None or not page.url or page.url in seen_pages:
            continue
        if len(normalise(page.text)) < MIN_EXCERPT_CHARS:
            continue  # nothing quotable: a page we could not read is not evidence
        seen_pages.add(page.url)
        corpus.append(page)

    findings: list[DraftFinding] = []
    seen_statements: set[str] = set()
    per_kind: dict[str, int] = {}

    for page in corpus:
        for extractor in _EXTRACTORS:
            for finding in extractor(page):
                key = normalise(finding.statement).casefold()
                if key in seen_statements:
                    continue
                cap = KIND_CAPS.get(finding.kind, _DEFAULT_KIND_CAP)
                if per_kind.get(finding.kind, 0) >= cap:
                    continue
                seen_statements.add(key)
                per_kind[finding.kind] = per_kind.get(finding.kind, 0) + 1
                findings.append(finding)

    for gap in find_information_gaps(corpus, findings):
        key = normalise(gap.statement).casefold()
        if key in seen_statements:
            continue
        seen_statements.add(key)
        findings.append(gap)

    return _cap_total(findings)


__all__ = [
    "KIND_CAPS",
    "MAX_FINDINGS",
    "MIN_EXCERPT_CHARS",
    "TEAM_SIZE_PREFIX",
    "clip",
    "derive_findings",
    "excerpt_around",
    "find_information_gaps",
    "make_finding",
    "opening_excerpt",
    "page_sentences",
]
