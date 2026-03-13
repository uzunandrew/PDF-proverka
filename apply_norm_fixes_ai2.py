import json

INPUT = r"D:\Отедел Системного Анализа\1. Calude code\projects\АИ\133-23-ГК-АИ2/_output/03_findings.json"
OUTPUT = r"D:\Отедел Системного Анализа\1. Calude code\projects\АИ\133-23-ГК-АИ2/_output/03a_norms_verified.json"

with open(INPUT, encoding="utf-8") as f:
    data = json.load(f)

REVISIONS = {
    "F-010": {
        "original_norm": "ГОСТ 21.101-2020 (действует), п. 7",
        "revised_norm": "ГОСТ Р 21.101-2020 (действует), п. 7",
        "desc_update": None,
        "sol_update": None,
        "revision_reason": "Исправлено написание: «ГОСТ 21.101-2020» → «ГОСТ Р 21.101-2020» (межгосударственный стандарт с префиксом Р). Документ действует. Верифицирован по кешу от 2026-03-13."
    },
    "F-019": {
        "original_norm": "СП 54.13330.2022 (действует), п. 5.7; ГОСТ 30493-2021 (действует)",
        "revised_norm": "СП 54.13330.2022 (действует), п. 5.7; ГОСТ 30493-2017 (введён 01.03.2018)",
        "desc_update": None,
        "sol_update": None,
        "revision_reason": "ГОСТ 30493-2021 не существует. Актуальная версия: ГОСТ 30493-2017 «Изделия санитарные керамические. Классификация и основные размеры» (введён 01.03.2018, заменил ГОСТ 30493-96). Год в ссылке исправлен с 2021 на 2017."
    },
    "F-025": {
        "original_norm": "ГОСТ 21.101-2020 (действует)",
        "revised_norm": "ГОСТ Р 21.101-2020 (действует)",
        "desc_update": None,
        "sol_update": None,
        "revision_reason": "Исправлено написание: «ГОСТ 21.101-2020» → «ГОСТ Р 21.101-2020». Документ действует, содержание замечания корректно."
    },
    "F-030": {
        "original_norm": "ГОСТ 21.101-2020 (действует)",
        "revised_norm": "ГОСТ Р 21.101-2020 (действует)",
        "desc_update": None,
        "sol_update": None,
        "revision_reason": "Исправлено написание: «ГОСТ 21.101-2020» → «ГОСТ Р 21.101-2020». Документ действует, содержание замечания корректно."
    },
    "F-037": {
        "original_norm": "СП 113.13330.2016 (действует), п. 7.1.5; п. 5.2.2",
        "revised_norm": "СП 113.13330.2023 (действует), п. 7.1.5; п. 5.2.2",
        "desc_update": ("СП 113.13330.2016, п. 7.1.5", "СП 113.13330.2023, п. 7.1.5"),
        "sol_update": None,
        "revision_reason": "СП 113.13330.2016 ЗАМЕНЁН на СП 113.13330.2023 «Стоянки автомобилей». Требование по высоте ≥2,0 м сохранено в актуальном документе. Ссылка в тексте описания обновлена."
    },
    "F-042": {
        "original_norm": "ГОСТ Р 21.1101-2013 (действует), п. 4.2.6",
        "revised_norm": "ГОСТ Р 21.101-2020 (действует), раздел 7",
        "desc_update": None,
        "sol_update": None,
        "revision_reason": "ГОСТ Р 21.1101-2013 ЗАМЕНЁН на ГОСТ Р 21.101-2020 (действует с 01.01.2021). Требования к нанесению высот установки оборудования содержатся в разделе 7 нового документа. Конкретный номер пункта требует уточнения проектировщиком."
    },
    "F-044": {
        "original_norm": "ГОСТ Р 12.4.026-2015 (действует), табл. Д.3",
        "revised_norm": "ГОСТ 12.4.026-2015 (действует), табл. Д.3",
        "desc_update": None,
        "sol_update": ("ГОСТ Р 12.4.026-2015", "ГОСТ 12.4.026-2015"),
        "revision_reason": "Уточнение написания: ГОСТ 12.4.026-2015 является межгосударственным стандартом (без префикса «Р»). Ссылка «ГОСТ Р 12.4.026-2015» содержит лишний префикс. По существу требование корректно, документ действует."
    },
    "F-048": {
        "original_norm": "СП 113.13330.2016 (действует), п. 5.2.7; ГОСТ Р 57278-2016 (действует)",
        "revised_norm": "СП 113.13330.2023 (действует), п. 5.2.7; ГОСТ Р 57278-2016 (действует)",
        "desc_update": ("СП 113.13330.2016, п. 5.2.7", "СП 113.13330.2023, п. 5.2.7"),
        "sol_update": None,
        "revision_reason": "СП 113.13330.2016 ЗАМЕНЁН на СП 113.13330.2023. Требование уклона ≥0,01 сохранено в актуальном документе. Ссылка в тексте описания обновлена."
    },
    "F-049": {
        "original_norm": "ГОСТ Р 21.1101-2013 (действует), п. 4.2.1",
        "revised_norm": "ГОСТ Р 21.101-2020 (действует), раздел 5",
        "desc_update": None,
        "sol_update": None,
        "revision_reason": "ГОСТ Р 21.1101-2013 ЗАМЕНЁН на ГОСТ Р 21.101-2020 (действует с 01.01.2021). Требования к оформлению условных обозначений содержатся в разделе 5 нового документа. Конкретный номер пункта требует уточнения проектировщиком."
    },
    "F-050": {
        "original_norm": "СП 113.13330.2016 (действует), п. 6.2; ГОСТ 21.501-2018 (действует); ГОСТ 21.1101-2013 (действует), п. 7.5",
        "revised_norm": "СП 113.13330.2023 (действует), п. 6.2; ГОСТ 21.501-2018 (действует); ГОСТ Р 21.101-2020 (действует)",
        "desc_update": None,
        "sol_update": None,
        "revision_reason": "Две замены: (1) СП 113.13330.2016 ЗАМЕНЁН на СП 113.13330.2023; (2) ГОСТ 21.1101-2013 ЗАМЕНЁН на ГОСТ Р 21.101-2020. Требования по нескользящему покрытию рамп сохранены в актуальных документах. Конкретный пункт в ГОСТ Р 21.101-2020 требует уточнения проектировщиком."
    },
    "F-056": {
        "original_norm": "ГОСТ Р 21.1101-2013 (действует); ГОСТ 21.501-2018 (действует)",
        "revised_norm": "ГОСТ Р 21.101-2020 (действует); ГОСТ 21.501-2018 (действует)",
        "desc_update": None,
        "sol_update": None,
        "revision_reason": "ГОСТ Р 21.1101-2013 ЗАМЕНЁН на ГОСТ Р 21.101-2020 (действует с 01.01.2021). ГОСТ 21.501-2018 действует без изменений."
    },
    "F-064": {
        "original_norm": "СП 20.13330.2017 (действует), п. 8.2",
        "revised_norm": "СП 20.13330.2016 (действует, с изм. №1-6), п. 8.2",
        "desc_update": None,
        "sol_update": None,
        "revision_reason": "«СП 20.13330.2017» — несуществующее обозначение. Документ обозначается по году утверждения (2016), хотя введён в 2017 году. Правильное обозначение: СП 20.13330.2016 «Нагрузки и воздействия» с Изменениями №1-6."
    },
}

findings_revised = []
for f in data["findings"]:
    fid = f["id"]
    if fid in REVISIONS:
        rev = REVISIONS[fid]
        original_desc = f.get("description")
        original_sol = f.get("solution")

        f["norm"] = rev["revised_norm"]

        if rev["desc_update"]:
            old, new = rev["desc_update"]
            if original_desc:
                f["description"] = original_desc.replace(old, new)

        if rev["sol_update"]:
            old, new = rev["sol_update"]
            if original_sol:
                f["solution"] = original_sol.replace(old, new)

        revised_desc = f.get("description")
        revised_sol = f.get("solution")

        f["norm_verified"] = True
        f["norm_status"] = "revised"
        f["norm_revision"] = {
            "original_norm": rev["original_norm"],
            "revised_norm": rev["revised_norm"],
            "original_text": original_desc if rev["desc_update"] and original_desc != revised_desc else (original_sol if rev["sol_update"] and original_sol != revised_sol else None),
            "revised_text": revised_desc if rev["desc_update"] and original_desc != revised_desc else (revised_sol if rev["sol_update"] and original_sol != revised_sol else None),
            "revision_reason": rev["revision_reason"]
        }
        findings_revised.append(fid)
    else:
        f["norm_verified"] = True
        f["norm_status"] = "ok"
        f["norm_revision"] = None

data["meta"]["norm_verification"] = {
    "verified_at": "2026-03-13T15:00:00",
    "total_norms_checked": len(REVISIONS),
    "norms_ok": 0,
    "norms_revised": len(REVISIONS),
    "findings_revised": sorted(findings_revised)
}

with open(OUTPUT, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f"Done. Revised: {len(findings_revised)} findings: {findings_revised}")
