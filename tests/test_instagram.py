import sys
from datetime import datetime, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import instagram

P = instagram.PARIS

def test_effet_48h_et_kpis():
    now = datetime.now(P)
    t_post = now - timedelta(days=3)
    sessions = [{"created": int((t_post + timedelta(hours=h)).timestamp())} for h in (1, 5, 30, 50)]
    sessions.append({"created": int((now - timedelta(days=10)).timestamp())})
    commandes = [{"created": int((t_post + timedelta(hours=5)).timestamp()), "quantite": 2, "source": "Instagram"},
                 {"created": int((now - timedelta(days=10)).timestamp()), "quantite": 1, "source": "Proches"}]
    ig = {"etat": {"username": "le_parachute", "derniere_collecte": now.isoformat(), "token_expire_le": (now + timedelta(days=50)).isoformat()},
          "abonnes": [{"date": (now - timedelta(days=9)).date().isoformat(), "abonnes": 100}, {"date": now.date().isoformat(), "abonnes": 130}],
          "compte": [{"date": (now - timedelta(days=1)).date().isoformat(), "reach": 500, "total_interactions": 40}],
          "nouveaux": {}, 
          "posts": [{"id": "a", "date": (now - timedelta(days=20)).isoformat(timespec="minutes"), "type": "FEED", "reach": 10, "total_interactions": 1},
                    {"id": "b", "date": t_post.isoformat(timespec="minutes"), "type": "FEED", "reach": 200, "total_interactions": 20}]}
    r = instagram.section(ig, sessions, commandes)
    b, a = r["posts"]
    assert b["id"] == "b" and b["effet"] == {"ouvertures": 3, "commandes": 1, "jeux": 2, "complet": True}
    assert a["effet"] is None  # publiée avant la première ouverture du lien
    assert b["engagement"] == 0.1
    assert r["kpis"]["abonnes"] == 130 and r["kpis"]["abonnes_7j"] == 30 and r["kpis"]["portee_7j"] == 500
    assert r["kpis"]["clients_instagram"] == 1 and r["kpis"]["clients_source_connue"] == 2
    assert r["alerte"] is None and len(r["jours"]) == instagram.JOURS_GRAPHE

def test_alertes():
    now = datetime.now(P)
    assert instagram.section({}, [], [])["alerte"].startswith("La collecte Instagram n'a pas encore")
    r = instagram.section({"etat": {"derniere_collecte": now.isoformat(), "token_expire_le": (now + timedelta(days=3)).isoformat()}}, [], [])
    assert "expire" in r["alerte"]
    r = instagram.section({"etat": {"derniere_collecte": (now - timedelta(days=3)).isoformat()}}, [], [])
    assert "pas tourné" in r["alerte"]
