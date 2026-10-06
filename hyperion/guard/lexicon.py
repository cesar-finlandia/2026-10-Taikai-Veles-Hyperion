"""Lexical scope signals (DP-GUARDRAILS §5.1–§5.2)."""
from __future__ import annotations
import re
from dataclasses import dataclass


DOMAIN_PATTERNS = (
  r"hyper[\s\-]?ai", r"hyperion", r"aeri[\s\-]?os", r"\bide\b", r"workspace", r"app\.ya?ml", r"\.ya?ml\b", r"\bya?ml\b",
  r"application profiles?", r"\bnative apps?(lications?)?\b", r"\bdevice apps?(lications?)?\b", r"\bprofiles?\b", r"manifests?",
  r"\bdeploy(ment|ments|ing|ed|s)?\b", r"\bcontainer(s|ized|ised)?\b", r"\bdocker(file|hub)?\b", r"\bimages?\b", r"\bnginx\b",
  r"\bkubernetes\b", r"\bk8s\b", r"\bedge\b", r"\bcloud\b", r"\biot\b", r"continuum", r"\bswarms?\b", r"\bdevices?\b", r"\bandroid\b",
  r"\bapk\b", r"\besp-?32\b", r"firmware", r"\bworkflows?\b", r"\bdashboards?\b", r"\bmetrics\b", r"\bvalidat(e|es|ed|ing|ion|or)\b",
  r"\bschemas?\b", r"\bdsl\b", r"\bspecifications?\b", r"\bspec\b", r"\bports?\b", r"\bcpu\b", r"\bmemory\b", r"\bstorage\b", r"\bqos\b",
  r"\bconstraints?\b", r"\blifecycle(phase)?\b", r"\bregistry\b", r"\bnodes?\b", r"orchestrat", r"\bmicroservices?\b", r"\bsse\b",
  r"\brag\b", r"\bllm\b", r"\bmqtt\b", r"\bsensors?\b", r"\bgpu\b", r"\barm64\b|\bamd64\b|\bx86_64\b",
  r"\b(redis|postgres(ql)?|mysql|mongo(db)?|grafana|prometheus|mosquitto|rabbitmq|traefik|caddy|influxdb)\b",
  r"cookbook", r"tutorial", r"\bdocs?\b", r"documentation", r"\bfiles?\b", r"\bfolders?\b", r"\bdirector(y|ies)\b", r"\bendpoint\b",
  r"\beclipse\b", r"\bveles\b", r"\bcerth\b", r"\bkapodistrian\b|\buoa\b", r"telef[oó]nica", r"\bentry ?point\b", r"\bargs\b",
  r"\bexecutiontype\b|\bbaseos\b|\bcontainerimage\b|\bschemaversion\b|\bpublicexposure\b|\bsupportedarchitectures\b",
)

OFFTOPIC_PATTERNS = (
  r"\bweather\b|\bforecast\b|\brain(ing|y)?\b|\bsnow(ing|y)?\b|\bsunny\b|\bhumidity\b|\bhurricane\b",
  r"\brecipes?\b|\bcook(ing)?\b|\bbak(e|ing)\b|\bpizza\b|\bpasta\b|\brestaurants?\b|\bdinner\b|\blunch\b|\bbreakfast\b|\bingredients?\b",
  r"\bfootball\b|\bsoccer\b|\bbasketball\b|\bnba\b|\bnfl\b|\btennis\b|\bcricket\b|\bbaseball\b|\bworld cup\b|\bolympic|\bpremier league\b|\bchampions league\b|\bmatch (score|result)",
  r"\bmovies?\b|\bfilms?\b|\bnetflix\b|\btv series\b|\bactors?\b|\bactress\b|\bcelebrit(y|ies)\b|\bsinger\b|\balbum\b|\bsongs?\b|\blyrics\b|\bconcert\b",
  r"\bjokes?\b|\briddles?\b|\bpoems?\b|\bpoetry\b|\blimerick\b|\bhaiku\b|\bfairy ?tale\b|\bbedtime story\b|\bsing me\b|\brap about\b",
  r"\bhoroscope\b|\bzodiac\b|\bastrology\b|\btarot\b",
  r"\bstock (price|market)s?\b|\bbitcoin\b|\bcrypto(currency)?\b|\bethereum\b|\binvest(ing|ment)?\b|\bforex\b|\blottery\b|\bcasino\b|\bbetting\b",
  r"\belections?\b|\bpresident\b|\bprime minister\b|\bpolitic(s|al|ian)\b|\btrump\b|\bbiden\b|\bputin\b",
  r"\btravel(ling|ing)?\b|\bhotels?\b|\bflights?\b|\bvacation\b|\bholidays?\b|\btourist\b|\bvisa\b|\bbeach(es)?\b",
  r"\btranslat(e|ion|ing)\b",
  r"\bhomework\b|\bessay\b|\bmath problem\b|\bsolve\b.{0,15}\d",
  r"\bmedical\b|\bsymptoms?\b|\bdiagnos(e|is)\b|\bmedicine\b|\bdisease\b|\bdoctor\b|\bpregnan|\bdiet\b|\bworkout\b|\bcalories\b|\bflu\b",
  r"\blegal advice\b|\blawyer\b|\bdivorce\b|\btax return\b",
  r"\bcapital of\b|\bpopulation of\b|\bwho (won|invented|discovered|painted|wrote)\b|\bhow (tall|old|far|big) is\b|\bmeaning of life\b|\bwhen was .{1,40} born\b",
  r"\bshopping\b|\bamazon\b|\bebay\b|\bdiscount\b|\bcoupon\b|\bprice of (a|an|the)\b",
  r"\bdating\b|\bgirlfriend\b|\bboyfriend\b|\brelationship advice\b",
  r"\bvideo games?\b|\bminecraft\b|\bfortnite\b|\bpokemon\b",
  r"\bheadlines?\b|\bbreaking news\b|\bnews today\b",
  r"\bwhat time is it\b|\bwhat day is\b|\bwhat('s| is) the date\b",
  r"\bquicksort\b|\bfibonacci\b|\bleetcode\b|\bsorting algorithm\b|\bbinary search\b|\bhello world in\b",
)

_DOMAIN_RES = tuple(re.compile(p, re.IGNORECASE) for p in DOMAIN_PATTERNS)
_OFFTOPIC_RES = tuple(re.compile(p, re.IGNORECASE) for p in OFFTOPIC_PATTERNS)


@dataclass(frozen=True)
class ScopeScore:
    domain_hits: tuple[str, ...]
    offtopic_hits: tuple[str, ...]

    @property
    def domain(self) -> int:
        return len(self.domain_hits)

    @property
    def offtopic(self) -> int:
        return len(self.offtopic_hits)


def lexical_scope(text: str) -> ScopeScore:
    """Case-insensitive regex scan of text against both pattern lists."""
    domain: list[str] = []
    seen_d: set[str] = set()
    for rx in _DOMAIN_RES:
        m = rx.search(text)
        if m:
            s = m.group(0).lower()
            if s not in seen_d:
                seen_d.add(s)
                domain.append(s)
    off: list[str] = []
    seen_o: set[str] = set()
    for rx in _OFFTOPIC_RES:
        m = rx.search(text)
        if m:
            s = m.group(0).lower()
            if s not in seen_o:
                seen_o.add(s)
                off.append(s)
    return ScopeScore(domain_hits=tuple(domain), offtopic_hits=tuple(off))
