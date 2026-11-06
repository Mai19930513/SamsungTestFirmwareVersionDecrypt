from .network import validate_device
from .storage import mysql_connection


COUNTRIES = {"CHC": "国行", "CHN": "国行", "TGY": "香港", "KOO": "韩国",
             "EUX": "欧洲", "INS": "印度", "XAA": "美国", "ATT": "美国"}


def load_models(settings, old_data):
    models = {}
    if settings.model_source == "mysql":
        connection = mysql_connection()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT name, code, cc FROM models")
                for row in cursor.fetchall():
                    models[row["code"]] = {"name": row["name"], "CC": row["cc"].split("|")}
        finally:
            connection.close()
    else:
        if settings.model_source == "auto":
            for model, regions in old_data.items():
                if isinstance(regions, dict) and model != "上次更新时间":
                    models[model] = {"name": next((data.get("机型", model) for data in regions.values() if isinstance(data, dict)), model), "CC": list(regions)}
        if settings.models_file.exists():
            for number, line in enumerate(settings.models_file.read_text(encoding="utf-8-sig").splitlines(), 1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = [part.strip() for part in line.split(",")]
                if len(parts) != 3 or not all(parts):
                    raise ValueError(f"Invalid models.txt line {number}")
                name, model, regions = parts
                entry = models.setdefault(model, {"name": name, "CC": []})
                entry["name"] = name
                entry["CC"] = list(dict.fromkeys(entry["CC"] + [region.strip().upper() for region in regions.split("|")]))
    for model, entry in models.items():
        for region in entry["CC"]:
            validate_device(model, region)
    if not models:
        raise ValueError("No models configured")
    return models
