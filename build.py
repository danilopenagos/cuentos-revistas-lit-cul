#!/usr/bin/env python3
"""
build.py — Cuentos hispanoamericanos en revistas
CSV (fuente de verdad) → site/data/db.json + site/data/cuentos.sqlite + reports/validacion.md

Uso:
    python build.py            # avisa de problemas pero genera la web
    python build.py --strict   # falla (exit 1) si hay advertencias

Entradas (carpeta data/):
    cuentos.csv   — dataset principal (una fila por publicación)
    revistas.csv  — una fila por revista: ciudad, país, años, repositorio digital, QID
    autores.csv   — una fila por autor: QID, VIAF (opcional)
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sqlite3
import sys
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUT = ROOT / "site" / "data"
REPORTS = ROOT / "reports"

# Cabecera original del CSV → nombre interno (snake_case, sin tildes)
COLUMNAS = {
    "Colaborador": "colaborador",
    "Sexo": "sexo",
    "PaisOrigen": "pais_origen",
    "Seudonimo": "seudonimo",
    "Título": "titulo",
    "Revista": "revista",
    "Fecha Publicación": "fecha",
    "Fascículo": "fasciculo",
    "Tipología": "tipologia",
    "Idioma": "idioma",
    "SeudonimoFirma": "firma",
    "TipoIndice": "rubrica",
    "CantitadPaginas": "n_paginas",
    "Paginacion": "paginacion",
}
OBLIGATORIAS = {"colaborador", "revista", "fecha", "paginacion"}
TIPOLOGIA_ESPERADA = "prosa ficción"
SIN_PAIS = "Sin asignar"
CAMPOS_REVISTAS = ["revista", "ciudad", "pais", "inicio", "fin", "repositorio", "url_digital", "wikidata_qid", "notas"]


# ---------------------------------------------------------------- utilidades
def slug(texto: str) -> str:
    """Slug determinista: sin tildes, minúsculas, guiones."""
    t = unicodedata.normalize("NFKD", texto or "")
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^a-zA-Z0-9]+", "-", t.lower()).strip("-")
    return t or "sin-nombre"


def leer_csv(ruta: Path) -> list[dict]:
    """Lee CSV/TSV detectando el separador. Todo como texto, sin espacios sobrantes."""
    if not ruta.exists():
        return []
    texto = ruta.read_text(encoding="utf-8-sig")
    try:
        dialecto = csv.Sniffer().sniff(texto.splitlines()[0], delimiters=",;\t")
    except csv.Error:
        dialecto = csv.excel
    filas = csv.DictReader(texto.splitlines(), dialect=dialecto)
    return [{(k or "").strip(): (v or "").strip() for k, v in f.items()} for f in filas]


def parse_paginacion(valor: str):
    """'23-25' → tramos [(23,25)]; '4' → [(4,4)]; '12-14, 30' → dos tramos (discontinua).
    Devuelve (tramos, error)."""
    if not valor:
        return [], "paginación vacía"
    tramos = []
    for parte in re.split(r"[,;]", valor):
        parte = parte.strip()
        m = re.fullmatch(r"(\d+)\s*[-–]\s*(\d+)", parte)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            if b < a:
                return [], f"rango invertido «{parte}»"
            tramos.append((a, b))
        elif re.fullmatch(r"\d+", parte):
            tramos.append((int(parte), int(parte)))
        else:
            return [], f"formato no reconocido «{parte}»"
    return tramos, None


def parse_fecha(valor: str):
    """Acepta AAAA, AAAA-MM, AAAA-MM-DD. Devuelve (iso, año, precisión, error)."""
    for fmt, prec in (("%Y-%m-%d", "día"), ("%Y-%m", "mes"), ("%Y", "año")):
        try:
            d = datetime.strptime(valor, fmt)
            return valor, d.year, prec, None
        except ValueError:
            continue
    return valor, None, None, f"fecha no reconocida «{valor}»"


def parse_float(valor: str):
    if not valor:
        return None
    try:
        return float(valor.replace(",", "."))
    except ValueError:
        return None


# --------------------------------------------------------------------- build
def build(strict: bool = False) -> int:
    avisos: dict[str, list[str]] = defaultdict(list)

    crudas = leer_csv(DATA / "cuentos.csv")
    if not crudas:
        print("ERROR: data/cuentos.csv no existe o está vacío", file=sys.stderr)
        return 1
    faltan = [c for c in COLUMNAS if c not in crudas[0]]
    extra = [c for c in crudas[0] if c not in COLUMNAS]
    if any(COLUMNAS[c] in OBLIGATORIAS for c in faltan):
        print(f"ERROR: faltan columnas obligatorias: {faltan}", file=sys.stderr)
        return 1
    if faltan:
        avisos["Estructura"].append(f"Columnas ausentes (se dejan vacías): {', '.join(faltan)}")
    if extra:
        avisos["Estructura"].append(f"Columnas no reconocidas (se ignoran): {', '.join(extra)}")

    filas = [{COLUMNAS[k]: f.get(k, "") for k in COLUMNAS} for f in crudas]

    # --- tablas auxiliares
    rev_meta = {slug(r["revista"]): r for r in leer_csv(DATA / "revistas.csv") if r.get("revista")}
    aut_meta = {slug(a["colaborador"]): a for a in leer_csv(DATA / "autores.csv") if a.get("colaborador")}

    # --- autores
    autores: dict[str, dict] = {}
    valores_autor: dict[str, dict[str, set]] = defaultdict(lambda: defaultdict(set))
    variantes_autor: dict[str, set] = defaultdict(set)
    for f in filas:
        nombre = f["colaborador"] or "Anónimo"
        aid = slug(nombre)
        variantes_autor[aid].add(nombre)
        for campo in ("sexo", "pais_origen", "seudonimo"):
            if f[campo]:
                valores_autor[aid][campo].add(f[campo])
        if aid not in autores:
            meta = aut_meta.get(aid, {})
            autores[aid] = {
                "id": aid, "nombre": nombre,
                "wikidata_qid": meta.get("wikidata_qid", ""), "viaf": meta.get("viaf", ""),
            }
    for aid, a in autores.items():
        if len(variantes_autor[aid]) > 1:
            avisos["Variantes de nombre unificadas (elige una grafía)"].append(
                " / ".join(f"«{x}»" for x in sorted(variantes_autor[aid])))
        v = valores_autor[aid]
        for campo in ("sexo", "pais_origen"):
            vals = sorted(v.get(campo, set()))
            if len(vals) > 1:
                avisos["Autores: valores contradictorios"].append(f"{a['nombre']} — {campo}: {' / '.join(vals)}")
            a[campo] = vals[0] if vals else ""
        a["seudonimos"] = sorted(v.get("seudonimo", set()))
        if not a["pais_origen"] or not a["sexo"]:
            avisos["Autores sin país de origen o sexo"].append(a["nombre"])
        if a["wikidata_qid"] and not re.fullmatch(r"Q\d+", a["wikidata_qid"]):
            avisos["Identificadores mal formados"].append(f"{a['nombre']}: QID «{a['wikidata_qid']}»")

    # --- revistas
    revistas: dict[str, dict] = {}
    variantes_revista: dict[str, set] = defaultdict(set)
    pendientes: list[str] = []
    for f in filas:
        nombre = f["revista"]
        rid = slug(nombre)
        variantes_revista[rid].add(nombre)
        if rid in revistas:
            continue
        meta = rev_meta.get(rid)
        if meta is None:
            avisos["Revistas que no están en revistas.csv"].append(nombre)
            pendientes.append(nombre)
            meta = {}
        url = meta.get("url_digital", "")
        if url and not re.match(r"https?://", url):
            avisos["Enlaces de digitalización mal formados"].append(f"{nombre}: «{url}»")
            url = ""
        revistas[rid] = {
            "id": rid, "titulo": nombre,
            "ciudad": meta.get("ciudad", ""), "pais": meta.get("pais", "") or SIN_PAIS,
            "inicio": meta.get("inicio", ""), "fin": meta.get("fin", ""),
            "repositorio": meta.get("repositorio", ""), "url_digital": url,
            "wikidata_qid": meta.get("wikidata_qid", ""),
        }
        if not meta.get("pais"):
            avisos["Revistas sin país de publicación"].append(nombre)
        if meta.get("pais") and not meta.get("ciudad"):
            avisos["Revistas sin ciudad"].append(nombre)
    for rid, vs in variantes_revista.items():
        if len(vs) > 1:
            avisos["Variantes de título de revista unificadas (elige una grafía)"].append(
                " / ".join(f"«{x}»" for x in sorted(vs)))
    for rid, meta in rev_meta.items():
        if rid not in revistas:
            avisos["Revistas en revistas.csv sin cuentos (¿grafía distinta?)"].append(meta["revista"])

    # --- cuentos
    cuentos = []
    usados: Counter = Counter()
    claves_dup: dict[tuple, list[int]] = defaultdict(list)
    for i, f in enumerate(filas, start=2):  # línea 1 = cabecera
        ref = f"línea {i}"
        rid = slug(f["revista"])
        aid = slug(f["colaborador"] or "Anónimo")

        tramos, err = parse_paginacion(f["paginacion"])
        if err:
            avisos["Paginación"].append(f"{ref}: {err}")
        pag_ini = tramos[0][0] if tramos else None
        pag_fin = tramos[-1][1] if tramos else None
        discontinua = len(tramos) > 1
        if discontinua:
            avisos["Paginación discontinua (revisar, no es error)"].append(f"{ref}: {f['paginacion']}")
        paginas_ocupadas = sum(b - a + 1 for a, b in tramos) if tramos else None

        n_pag = parse_float(f["n_paginas"])
        if f["n_paginas"] and n_pag is None:
            avisos["Extensión"].append(f"{ref}: CantitadPaginas no numérica «{f['n_paginas']}»")
        if n_pag is not None and paginas_ocupadas and n_pag > paginas_ocupadas + 0.05:
            avisos["Extensión mayor que el rango de páginas"].append(
                f"{ref}: {n_pag} pág. en «{f['paginacion']}»")

        iso, anio, prec, err = parse_fecha(f["fecha"])
        if err:
            avisos["Fechas"].append(f"{ref}: {err}")

        if f["tipologia"] and f["tipologia"].lower() != TIPOLOGIA_ESPERADA:
            avisos["Tipología distinta de «Prosa ficción»"].append(f"{ref}: «{f['tipologia']}»")
        if not f["titulo"]:
            avisos["Cuentos sin título"].append(f"{ref}: {f['colaborador']}, {f['revista']} n.º {f['fasciculo']}")
        if not f["rubrica"]:
            avisos["_rubrica_vacia"].append(ref)

        # ID determinista: revista-fasciculo-pPágina (+ sufijo si colisiona)
        base = f"{rid}-{slug(f['fasciculo'] or 's-n')}-p{pag_ini if pag_ini is not None else 'x'}"
        usados[base] += 1
        cid = base if usados[base] == 1 else f"{base}-{usados[base]}"

        claves_dup[(aid, slug(f["titulo"]), rid, f["fasciculo"])].append(i)

        cuentos.append({
            "id": cid, "titulo": f["titulo"], "autor_id": aid, "revista_id": rid,
            "firma": f["firma"], "fecha": iso, "anio": anio, "fecha_precision": prec,
            "fasciculo": f["fasciculo"], "rubrica": f["rubrica"], "tipologia": f["tipologia"],
            "idioma": f["idioma"], "n_paginas": n_pag, "paginacion": f["paginacion"],
            "pag_ini": pag_ini, "pag_fin": pag_fin, "discontinua": discontinua,
            "linea_csv": i,
        })

    for (aid, _t, rid, fasc), lineas in claves_dup.items():
        if len(lineas) > 1:
            avisos["Posibles duplicados o texto en varios tramos"].append(
                f"{autores[aid]['nombre']} en {revistas[rid]['titulo']} n.º {fasc}: líneas {', '.join(map(str, lineas))}")

    vacias = avisos.pop("_rubrica_vacia", [])
    if vacias:
        avisos["Rúbrica (TipoIndice) vacía"].append(f"{len(vacias)} de {len(cuentos)} cuentos")

    # --- agregados para la navegación
    por_revista = Counter(c["revista_id"] for c in cuentos)
    por_autor = Counter(c["autor_id"] for c in cuentos)
    for r in revistas.values():
        r["n_cuentos"] = por_revista[r["id"]]
    for a in autores.values():
        a["n_cuentos"] = por_autor[a["id"]]
    paises: dict[str, dict] = {}
    for r in revistas.values():
        p = paises.setdefault(slug(r["pais"]), {"id": slug(r["pais"]), "nombre": r["pais"],
                                                "n_revistas": 0, "n_cuentos": 0})
        p["n_revistas"] += 1
        p["n_cuentos"] += r["n_cuentos"]

    # --- salida
    OUT.mkdir(parents=True, exist_ok=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    generado = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    db = {
        "meta": {"generado": generado, "n_cuentos": len(cuentos), "n_autores": len(autores),
                 "n_revistas": len(revistas), "n_paises": len(paises)},
        "paises": sorted(paises.values(), key=lambda p: (p["nombre"] == SIN_PAIS, p["nombre"])),
        "revistas": sorted(revistas.values(), key=lambda r: r["titulo"]),
        "autores": sorted(autores.values(), key=lambda a: a["nombre"]),
        "cuentos": cuentos,
    }
    (OUT / "db.json").write_text(json.dumps(db, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    escribir_sqlite(OUT / "cuentos.sqlite", db)
    n_avisos = escribir_informe(REPORTS / "validacion.md", avisos, db["meta"])
    plantilla = REPORTS / "revistas_pendientes.csv"
    plantilla.unlink(missing_ok=True)
    if pendientes:
        with plantilla.open("w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(CAMPOS_REVISTAS)
            for nombre in sorted(pendientes, key=str.lower):
                w.writerow([nombre] + [""] * (len(CAMPOS_REVISTAS) - 1))
        print(f"    {len(pendientes)} revistas sin ficha → reports/revistas_pendientes.csv (copia sus filas a data/revistas.csv)")

    m = db["meta"]
    print(f"OK: {m['n_cuentos']} cuentos · {m['n_autores']} autores · {m['n_revistas']} revistas · {m['n_paises']} países")
    print(f"    {n_avisos} avisos → reports/validacion.md")
    return 1 if (strict and n_avisos) else 0


def escribir_sqlite(ruta: Path, db: dict) -> None:
    ruta.unlink(missing_ok=True)
    con = sqlite3.connect(ruta)
    con.executescript("""
    CREATE TABLE revistas (id TEXT PRIMARY KEY, titulo TEXT, ciudad TEXT, pais TEXT,
        inicio TEXT, fin TEXT, repositorio TEXT, url_digital TEXT, wikidata_qid TEXT, n_cuentos INTEGER);
    CREATE TABLE autores (id TEXT PRIMARY KEY, nombre TEXT, sexo TEXT, pais_origen TEXT,
        seudonimos TEXT, wikidata_qid TEXT, viaf TEXT, n_cuentos INTEGER);
    CREATE TABLE cuentos (id TEXT PRIMARY KEY, titulo TEXT,
        autor_id TEXT REFERENCES autores(id), revista_id TEXT REFERENCES revistas(id),
        firma TEXT, fecha TEXT, anio INTEGER, fecha_precision TEXT, fasciculo TEXT,
        rubrica TEXT, tipologia TEXT, idioma TEXT, n_paginas REAL, paginacion TEXT,
        pag_ini INTEGER, pag_fin INTEGER, discontinua INTEGER, linea_csv INTEGER);
    """)
    con.executemany("INSERT INTO revistas VALUES (:id,:titulo,:ciudad,:pais,:inicio,:fin,:repositorio,:url_digital,:wikidata_qid,:n_cuentos)",
                    db["revistas"])
    con.executemany("INSERT INTO autores VALUES (:id,:nombre,:sexo,:pais_origen,:seudonimos,:wikidata_qid,:viaf,:n_cuentos)",
                    [{**a, "seudonimos": "; ".join(a["seudonimos"])} for a in db["autores"]])
    con.executemany("""INSERT INTO cuentos VALUES (:id,:titulo,:autor_id,:revista_id,:firma,:fecha,:anio,
        :fecha_precision,:fasciculo,:rubrica,:tipologia,:idioma,:n_paginas,:paginacion,:pag_ini,:pag_fin,
        :discontinua,:linea_csv)""", db["cuentos"])
    # Vista plana: lo más cómodo para consultar en Datasette
    con.execute("""CREATE VIEW cuentos_completo AS
        SELECT c.id, c.titulo, a.nombre AS autor, c.firma, a.sexo, a.pais_origen AS pais_autor,
               r.titulo AS revista, r.ciudad, r.pais AS pais_revista, r.url_digital, c.fecha, c.anio, c.fasciculo,
               c.rubrica, c.idioma, c.n_paginas, c.paginacion
        FROM cuentos c JOIN autores a ON a.id = c.autor_id JOIN revistas r ON r.id = c.revista_id""")
    con.commit()
    con.close()


def escribir_informe(ruta: Path, avisos: dict, meta: dict) -> int:
    total = sum(len(v) for v in avisos.values())
    lineas = [f"# Informe de validación", "",
              f"Generado: {meta['generado']} · {meta['n_cuentos']} cuentos · {meta['n_autores']} autores · "
              f"{meta['n_revistas']} revistas", "", f"**{total} avisos**", ""]
    for seccion, items in avisos.items():
        lineas += [f"## {seccion} ({len(items)})", ""] + [f"- {x}" for x in items] + [""]
    if not avisos:
        lineas.append("Sin problemas detectados.")
    ruta.write_text("\n".join(lineas), encoding="utf-8")
    return total


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--strict", action="store_true", help="falla si hay avisos")
    sys.exit(build(ap.parse_args().strict))
