#!/usr/bin/env python3
"""
Alerte Bornes de Recharge - Sarthe (72)
Utilise le fichier CSV local si present, sinon telecharge depuis data.gouv.fr
+ Geolocalisation et navigation
"""

import requests
import json
import csv
import io
import os
import glob
from datetime import datetime
from zoneinfo import ZoneInfo

# ============================================================
# CONFIGURATION
# ============================================================

SUJET_NOUVELLES = "alerte-bornes-sarthe"
SUJET_RETRAITS = "alerte-bornes-sarthe-maj"

FICHIER_MEMOIRE = "bornes_memoire.json"
DOSSIER_PUBLIC = "public"

URL_DATASET = "https://www.data.gouv.fr/api/1/datasets/fichier-consolide-des-bornes-de-recharge-pour-vehicules-electriques/"

# ============================================================


def charger_json(fichier, defaut):
    if os.path.exists(fichier):
        try:
            with open(fichier, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return defaut
    return defaut


def sauvegarder_json(fichier, data):
    with open(fichier, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def envoyer_notification(sujet, titre, message):
    try:
        requests.post(
            f"https://ntfy.sh/{sujet}",
            data=message.encode("utf-8"),
            headers={
                "Title": titre.encode("utf-8"),
                "Priority": "high",
                "Tags": "zap,car,electric_plug",
            },
            timeout=10,
        )
        print(f"   -> Notif envoyee sur {sujet} : {titre}")
    except Exception as e:
        print(f"   [ERREUR] Envoi notif : {e}")


def trouver_fichier_local():
    """Cherche un fichier CSV IRVE deja telecharge dans le dossier courant."""
    for f in glob.glob("*.csv"):
        if "irve" in f.lower() and "documentation" not in f.lower():
            taille = os.path.getsize(f)
            if taille > 10 * 1024 * 1024:  # > 10 Mo
                return f
    return None


def trouver_url_fichier_irve():
    """Trouve l'URL du fichier IRVE consolide le plus recent."""
    try:
        r = requests.get(URL_DATASET, timeout=30)
        r.raise_for_status()
        data = r.json()

        resources = data.get("resources", [])
        candidats = []
        for res in resources:
            title = (res.get("title") or "").lower()
            fmt = (res.get("format") or "").lower()
            url = res.get("url")
            if not url:
                continue
            if "documentation" in title:
                continue
            if fmt != "csv":
                continue
            if "consolidation" not in title:
                continue
            date = res.get("last_modified") or res.get("created_at") or ""
            candidats.append((date, url, res.get("title")))

        if not candidats:
            print("[ERREUR] Aucune ressource CSV consolidee trouvee")
            return None

        candidats.sort(reverse=True)
        print(f"   Fichier retenu : {candidats[0][2]}")
        return candidats[0][1]

    except Exception as e:
        print(f"[ERREUR] API data.gouv.fr : {e}")
        return None


def extraire_bornes_sarthe(content):
    """Extrait les bornes de la Sarthe depuis le contenu CSV."""
    bornes = []
    total = 0

    sample = content[:4096].decode("utf-8-sig", errors="ignore")
    sep = "," if sample.count(",") > sample.count(";") else ";"

    reader = csv.DictReader(
        io.StringIO(content.decode("utf-8-sig", errors="replace")),
        delimiter=sep,
    )

    for row in reader:
        total += 1
        cp = str(row.get("consolidated_code_postal", "") or "").strip()
        if cp.startswith("72"):
            bornes.append(row)

    print(f"   Total lignes : {total} | Bornes en Sarthe : {len(bornes)}")
    return bornes


def charger_bornes():
    """Charge les bornes depuis le fichier local ou via telechargement."""
    fichier_local = trouver_fichier_local()
    if fichier_local:
        print(f"   Fichier local trouve : {fichier_local}")
        taille_mo = os.path.getsize(fichier_local) / 1024 / 1024
        print(f"   Taille : {taille_mo:.1f} Mo")
        try:
            with open(fichier_local, "rb") as f:
                content = f.read()
            return extraire_bornes_sarthe(content)
        except Exception as e:
            print(f"   [ERREUR] Lecture locale : {e}")
            return []

    print("   Aucun fichier local, telechargement depuis data.gouv.fr...")
    url = trouver_url_fichier_irve()
    if not url:
        return []

    try:
        r = requests.get(url, timeout=300, stream=True)
        r.raise_for_status()
        content = r.content
        print(f"   Fichier telecharge ({len(content) / 1024 / 1024:.1f} Mo)")
        return extraire_bornes_sarthe(content)
    except Exception as e:
        print(f"[ERREUR] Telechargement : {e}")
        return []


def extraire_infos(borne):
    return {
        "id": borne.get("id_pdc_itinerance") or borne.get("id_pdc_local") or "",
        "station_id": borne.get("id_station_itinerance") or borne.get("nom_station", ""),
        "nom_station": borne.get("nom_station", "?"),
        "adresse": borne.get("adresse_station", "?"),
        "commune": borne.get("consolidated_commune", "?"),
        "cp": borne.get("consolidated_code_postal", "?"),
        "lat": borne.get("consolidated_latitude", ""),
        "lon": borne.get("consolidated_longitude", ""),
        "puissance": float(borne.get("puissance_nominale", 0) or 0),
        "operateur": borne.get("nom_operateur", "?"),
        "enseigne": borne.get("nom_enseigne", "?"),
        "prise_type_2": str(borne.get("prise_type_2", "")).upper() == "TRUE",
        "prise_type_ccs": str(borne.get("prise_type_combo_ccs", "")).upper() == "TRUE",
        "prise_type_chademo": str(borne.get("prise_type_chademo", "")).upper() == "TRUE",
        "prise_type_ef": str(borne.get("prise_type_ef", "")).upper() == "TRUE",
    }


def grouper_par_station(bornes):
    stations = {}
    for b in bornes:
        info = extraire_infos(b)
        sid = info["station_id"] or f"{info['lat']}_{info['lon']}"
        if sid not in stations:
            stations[sid] = {
                "id": sid,
                "nom": info["nom_station"],
                "adresse": info["adresse"],
                "commune": info["commune"],
                "cp": info["cp"],
                "lat": info["lat"],
                "lon": info["lon"],
                "operateur": info["operateur"],
                "enseigne": info["enseigne"],
                "puissance_max": 0,
                "nbre_pdc": 0,
                "prises": set(),
            }
        s = stations[sid]
        if info["puissance"] > s["puissance_max"]:
            s["puissance_max"] = info["puissance"]
        s["nbre_pdc"] += 1
        if info["prise_type_2"]:
            s["prises"].add("Type 2")
        if info["prise_type_ccs"]:
            s["prises"].add("CCS")
        if info["prise_type_chademo"]:
            s["prises"].add("CHAdeMO")
        if info["prise_type_ef"]:
            s["prises"].add("EF")

    for s in stations.values():
        s["prises"] = sorted(list(s["prises"]))

    return stations


def generer_page_html(stations):
    """Genere la page web optimisee mobile avec geolocalisation."""
    os.makedirs(DOSSIER_PUBLIC, exist_ok=True)

    paris = datetime.now(ZoneInfo("Europe/Paris"))
    date_heure = paris.strftime("%d/%m/%Y a %Hh%M")

    stations_triees = sorted(stations.values(), key=lambda s: -s["puissance_max"])

    nb_total = len(stations_triees)
    nb_rapides = len([s for s in stations_triees if s["puissance_max"] >= 50])
    nb_ultra = len([s for s in stations_triees if s["puissance_max"] >= 150])

    # Construire les donnees JSON pour le JS
    stations_json = []
    for s in stations_triees:
        lat = None
        lon = None
        try:
            if s.get("lat"):
                lat = float(s["lat"])
            if s.get("lon"):
                lon = float(s["lon"])
        except (ValueError, TypeError):
            pass

        stations_json.append({
            "id": s["id"],
            "nom": s["nom"],
            "adresse": s["adresse"],
            "commune": s["commune"],
            "cp": s["cp"],
            "lat": lat,
            "lon": lon,
            "puissance": s["puissance_max"],
            "nbre_pdc": s["nbre_pdc"],
            "operateur": s["operateur"],
            "prises": s["prises"],
        })

    cards = []
    for s in stations_triees:
        if s["puissance_max"] >= 150:
            couleur = "#c00"
            badge = "ULTRA"
        elif s["puissance_max"] >= 50:
            couleur = "#e80"
            badge = "RAPIDE"
        else:
            couleur = "#090"
            badge = "NORMALE"

        prises_html = " ".join([f'<span class="prise">{p}</span>' for p in s["prises"]])

        nav_url = ""
        try:
            if s.get("lat") and s.get("lon"):
                lat_f = float(s["lat"])
                lon_f = float(s["lon"])
                nav_url = f"https://www.google.com/maps/dir/?api=1&destination={lat_f},{lon_f}&travelmode=driving"
        except (ValueError, TypeError):
            pass

        nav_btn = ""
        if nav_url:
            nav_btn = f'<a href="{nav_url}" target="_blank" class="nav-btn">🧭 Naviguer</a>'

        cards.append(f'''
        <div class="station" style="border-left-color: {couleur};" data-puissance="{s["puissance_max"]:.0f}" data-id="{s["id"]}">
            <div class="badge" style="background: {couleur};">{badge}</div>
            <div class="nom">{s["nom"]}</div>
            <div class="puissance">{s["puissance_max"]:.0f} kW</div>
            <div class="adresse">{s["adresse"]}</div>
            <div class="commune">{s["commune"]} ({s["cp"]})</div>
            <div class="prises">{prises_html}</div>
            <div class="infos">
                <span>🔌 {s["nbre_pdc"]} PDC</span>
                <span>🏢 {s["operateur"]}</span>
            </div>
            <div class="actions">
                {nav_btn}
                <span class="distance" data-id="{s["id"]}"></span>
            </div>
        </div>''')

    stations_js = json.dumps(stations_json, ensure_ascii=False)

    html = f'''<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Bornes Sarthe</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif;
         background: #f0f4f8; color: #222; padding: 12px;
         max-width: 700px; margin: 0 auto; }}
  header {{ background: linear-gradient(135deg, #0369a1, #075985);
            color: white; padding: 16px; border-radius: 12px;
            margin-bottom: 12px; text-align: center; }}
  h1 {{ font-size: 1.3em; margin-bottom: 6px; }}
  .stats {{ font-size: 0.9em; opacity: 0.95; }}
  .stats span {{ display: inline-block; margin: 2px 6px; }}

  .btn-geoloc {{ width: 100%; padding: 14px; margin-bottom: 12px;
                 background: linear-gradient(135deg, #059669, #047857);
                 color: white; border: none; border-radius: 10px;
                 font-size: 1em; font-weight: bold; cursor: pointer;
                 box-shadow: 0 2px 6px rgba(0,0,0,0.15); }}
  .btn-geoloc:active {{ transform: scale(0.98); }}
  .btn-geoloc.actif {{ background: linear-gradient(135deg, #0369a1, #075985); }}

  .filtres {{ display: flex; gap: 6px; margin-bottom: 12px; flex-wrap: wrap; }}
  .filtre {{ flex: 1; min-width: 80px; padding: 8px 10px; border: none;
             background: white; border-radius: 8px; font-size: 0.8em;
             font-weight: bold; cursor: pointer; color: #333;
             box-shadow: 0 1px 3px rgba(0,0,0,0.08); }}
  .filtre.actif {{ background: #0369a1; color: white; }}

  .station {{ background: white; padding: 12px; margin-bottom: 8px;
              border-radius: 10px; border-left: 4px solid #ccc;
              box-shadow: 0 1px 3px rgba(0,0,0,0.06); position: relative; }}
  .badge {{ position: absolute; top: 8px; right: 8px;
            font-size: 0.65em; color: white; padding: 2px 6px;
            border-radius: 4px; font-weight: bold; }}
  .nom {{ font-weight: bold; font-size: 0.95em; padding-right: 80px; }}
  .puissance {{ font-size: 1.4em; font-weight: bold; color: #0369a1;
                margin: 4px 0; }}
  .adresse {{ font-size: 0.85em; color: #555; }}
  .commune {{ font-size: 0.85em; color: #888; margin-bottom: 6px; }}
  .prises {{ margin: 6px 0; }}
  .prise {{ display: inline-block; background: #e0f2fe; color: #0369a1;
            padding: 2px 6px; border-radius: 4px; font-size: 0.75em;
            margin-right: 4px; margin-bottom: 2px; }}
  .infos {{ font-size: 0.8em; color: #666; display: flex;
            justify-content: space-between; margin-top: 6px; }}
  .actions {{ display: flex; align-items: center; justify-content: space-between;
              margin-top: 10px; padding-top: 8px;
              border-top: 1px solid #eee; }}
  .nav-btn {{ display: inline-block; background: #0369a1; color: white;
              padding: 8px 14px; border-radius: 6px; text-decoration: none;
              font-size: 0.85em; font-weight: bold; }}
  .nav-btn:active {{ background: #075985; }}
  .distance {{ font-size: 0.9em; color: #059669; font-weight: bold; }}
  .date {{ text-align: center; color: #888; font-size: 0.8em;
           margin: 16px 0 8px 0; }}
  .cache {{ display: none; }}
  .proche {{ border-left-width: 6px !important; }}
</style>
</head>
<body>
<header>
  <h1>⚡ Bornes Sarthe</h1>
  <div class="stats">
    <span>📍 {nb_total} stations</span>
    <span>⚡ {nb_rapides} rapides</span>
    <span>🚀 {nb_ultra} ultra</span>
  </div>
</header>

<button class="btn-geoloc" id="btn-geoloc" onclick="localiser()">
  📍 Trouver les bornes autour de moi
</button>

<div class="filtres">
  <button class="filtre actif" onclick="filtre('toutes', this)">Toutes</button>
  <button class="filtre" onclick="filtre('rapide', this)">⚡ Rapides</button>
  <button class="filtre" onclick="filtre('ultra', this)">🚀 Ultra</button>
</div>

<div id="liste">
{"".join(cards)}
</div>

<div class="date">Mise a jour : {date_heure}</div>

<script>
const STATIONS = {stations_js};

function distanceKm(lat1, lon1, lat2, lon2) {{
  const R = 6371;
  const dLat = (lat2 - lat1) * Math.PI / 180;
  const dLon = (lon2 - lon1) * Math.PI / 180;
  const a = Math.sin(dLat/2)**2 +
            Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
            Math.sin(dLon/2)**2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1-a));
}}

function localiser() {{
  const btn = document.getElementById('btn-geoloc');
  btn.textContent = '⏳ Localisation en cours...';
  btn.disabled = true;

  if (!navigator.geolocation) {{
    btn.textContent = '❌ Geolocalisation non supportee';
    return;
  }}

  navigator.geolocation.getCurrentPosition(
    (pos) => {{
      const uLat = pos.coords.latitude;
      const uLon = pos.coords.longitude;

      const distances = {{}};
      STATIONS.forEach(s => {{
        if (s.lat && s.lon) {{
          distances[s.id] = distanceKm(uLat, uLon, s.lat, s.lon);
        }}
      }});

      document.querySelectorAll('.station').forEach(card => {{
        const sid = card.dataset.id;
        const d = distances[sid];
        const span = card.querySelector('.distance');
        if (d !== undefined) {{
          if (d < 1) span.textContent = '📍 ' + Math.round(d*1000) + ' m';
          else span.textContent = '📍 ' + d.toFixed(1) + ' km';
        }}
      }});

      const liste = document.getElementById('liste');
      const cards = Array.from(liste.querySelectorAll('.station'));
      cards.sort((a, b) => {{
        const da = distances[a.dataset.id] !== undefined ? distances[a.dataset.id] : 99999;
        const db = distances[b.dataset.id] !== undefined ? distances[b.dataset.id] : 99999;
        return da - db;
      }});
      liste.innerHTML = '';
      cards.forEach(c => liste.appendChild(c));

      cards.slice(0, 3).forEach(c => c.classList.add('proche'));

      btn.textContent = '✅ Trie par distance — actualiser';
      btn.classList.add('actif');
      btn.disabled = false;
    }},
    (err) => {{
      btn.textContent = '❌ Position refusee — reessayer';
      btn.disabled = false;
    }},
    {{ enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 }}
  );
}}

function filtre(type, btn) {{
  document.querySelectorAll('.filtre').forEach(b => b.classList.remove('actif'));
  btn.classList.add('actif');
  document.querySelectorAll('.station').forEach(s => {{
    const p = parseFloat(s.dataset.puissance);
    if (type === 'toutes') s.classList.remove('cache');
    else if (type === 'rapide') s.classList.toggle('cache', p < 50);
    else if (type === 'ultra') s.classList.toggle('cache', p < 150);
  }});
}}
</script>
</body>
</html>'''

    with open(os.path.join(DOSSIER_PUBLIC, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    print(f"   Page HTML generee ({len(stations_triees)} stations)")


def main():
    print("=== Alerte Bornes de Recharge - Sarthe ===")
    paris = datetime.now(ZoneInfo("Europe/Paris"))
    print(f"Execution : {paris.strftime('%d/%m/%Y %H:%M')}\n")

    memoire = charger_json(FICHIER_MEMOIRE, {})
    print(f"Memoire : {len(memoire)} stations connues\n")

    bornes = charger_bornes()
    if not bornes:
        print("Aucune borne recuperee.")
        return

    stations_actuelles = grouper_par_station(bornes)
    print(f"\n{len(stations_actuelles)} stations en Sarthe\n")

    ids_actuels = set(stations_actuelles.keys())
    ids_memoire = set(memoire.keys())

    nouvelles = ids_actuels - ids_memoire
    retirees = ids_memoire - ids_actuels

    print(f"Nouvelles : {len(nouvelles)}")
    print(f"Retirees : {len(retirees)}\n")

    if nouvelles:
        lignes = []
        for sid in list(nouvelles)[:20]:
            s = stations_actuelles[sid]
            lignes.append(
                f"{s['nom']}\n"
                f"   {s['adresse']}, {s['commune']}\n"
                f"   {s['puissance_max']:.0f} kW - {s['nbre_pdc']} PDC\n"
                f"   {s['operateur']}"
            )
        titre = f"{len(nouvelles)} nouvelle(s) borne(s) en Sarthe"
        message = "\n\n".join(lignes)
        if len(nouvelles) > 20:
            message += f"\n\n... et {len(nouvelles) - 20} autres"
        print(f"\n{titre}\n")
        envoyer_notification(SUJET_NOUVELLES, titre, message)

    if retirees:
        lignes = []
        for sid in list(retirees)[:20]:
            s = memoire[sid]
            lignes.append(
                f"{s.get('nom', '?')}\n"
                f"   {s.get('adresse', '?')}, {s.get('commune', '?')}"
            )
        titre = f"{len(retirees)} borne(s) retiree(s) en Sarthe"
        message = "\n\n".join(lignes)
        if len(retirees) > 20:
            message += f"\n\n... et {len(retirees) - 20} autres"
        print(f"\n{titre}\n")
        envoyer_notification(SUJET_RETRAITS, titre, message)

    if not nouvelles and not retirees:
        print("Aucun changement detecte.")

    print("\n-> Generation de la page web...")
    generer_page_html(stations_actuelles)

    sauvegarder_json(FICHIER_MEMOIRE, stations_actuelles)
    print(f"\nMemoire sauvegardee : {len(stations_actuelles)} stations")


if __name__ == "__main__":
    main()