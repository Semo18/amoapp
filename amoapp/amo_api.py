# -*- coding: utf-8 -*-
import math
from typing import Any, Dict, Iterator, Optional

from .constants import DEFAULT_DIVISOR, LEADS_LIMIT
from .http_client import HttpClient
from .utils import to_float, to_int


class AmoApi:
    def __init__(self, api_base: str, token: str) -> None:
        self.base = api_base.rstrip("/")
        self.http = HttpClient(token)

    # --- Pipelines ---------------------------------------------------------

    def get_pipelines(self) -> Iterator[Dict[str, Any]]:
        """Итератор по всем воронкам."""
        url = f"{self.base}/leads/pipelines?limit=250"
        while url:
            data = self.http.get_json(url)
            items = data.get("_embedded", {}).get("pipelines", []) or []
            for p in items:
                yield p
            url = data.get("_links", {}).get("next", {}).get("href")

    def resolve_pipeline_id(self, name: str) -> Optional[int]:
        """По имени воронки вернуть её ID (или None)."""
        name = (name or "").strip()
        if not name:
            return None
        for p in self.get_pipelines():
            if p.get("name") == name:
                try:
                    return int(p["id"])
                except Exception:
                    return None
        return None

    # --- Leads -------------------------------------------------------------

    def fetch_updated_leads(
        self,
        since_unix: int,
        pipeline_id: Optional[int] = None,
    ) -> Iterator[Dict[str, Any]]:
        """Сделки, обновлённые после since_unix, опционально по воронке."""
        url = (
            f"{self.base}/leads?with=custom_fields_values"
            f"&limit={LEADS_LIMIT}"
            f"&filter[updated_at][from]={since_unix}"
        )
        if pipeline_id:
            url += f"&filter[pipeline_id][]={int(pipeline_id)}"

        while url:
            data = self.http.get_json(url)
            leads = data.get("_embedded", {}).get("leads", []) or []
            for lead in leads:
                yield lead
            url = data.get("_links", {}).get("next", {}).get("href")

    def patch_lead_fields(self, lead_id: int,
                          fields: Dict[int, Any]) -> None:
        """Универсальный PATCH кастомных полей сделки."""
        cf_vals = []
        for fid, val in fields.items():
            cf_vals.append(
                {"field_id": int(fid), "values": [{"value": val}]}
            )
        payload = [{"id": int(lead_id), "custom_fields_values": cf_vals}]
        self.http.patch_json(f"{self.base}/leads", payload)


# --- Helpers ---------------------------------------------------------------

def first_cf_value(lead: Dict[str, Any], field_id: int) -> Optional[Any]:
    cfs = lead.get("custom_fields_values") or []
    for cf in cfs:
        if cf.get("field_id") == field_id:
            vals = cf.get("values") or []
            if vals:
                return vals[0].get("value")
    return None


def lead_budget(
    lead: Dict[str, Any],
    custom_main_budget_id: Optional[int],
) -> float:
    raw = (first_cf_value(lead, custom_main_budget_id)
           if custom_main_budget_id else lead.get("price"))
    return to_float(raw, 0.0)


def calc_installment(
    budget: float,
    formula_type: str,
    divisor: float,
    percent: float,
) -> int:
    if budget <= 0:
        return 0

    if formula_type == "fixed_divisor":
        d = divisor if divisor and divisor > 0 else DEFAULT_DIVISOR
        return int(math.ceil(budget / d))

    if formula_type == "percent":
        return int(math.ceil(budget * (percent / 100.0)))

    # fallback
    return int(math.ceil(budget / DEFAULT_DIVISOR))


def to_int_safe(value: Any) -> int:
    return to_int(value, 0)
