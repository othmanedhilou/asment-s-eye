"""Rapport PDF pour le service HSE.

L'export CSV sert à qui veut retravailler les données. Un responsable HSE, lui,
veut un document qu'il peut lire, classer et présenter en réunion — avec les
chiffres qui engagent une décision, pas la liste de tous les événements.

Le rapport répond donc à quatre questions, dans cet ordre :
    combien d'alertes, et de quelle gravité ?
    où — quelle caméra, quelle zone ?
    le système est-il crédible (part de fausses alertes) ?
    les alertes atteignent-elles quelqu'un (délai de prise en charge) ?
"""

from datetime import datetime, timedelta
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.storage import quality_stats, read_alerts, stats_summary

ENCRE = colors.HexColor("#1A1D21")
GRIS = colors.HexColor("#6B7280")
TRAIT = colors.HexColor("#D8D5CF")
CRITIQUE = colors.HexColor("#B42318")
HAUTE = colors.HexColor("#B54708")
MOYENNE = colors.HexColor("#175CD3")

COULEUR_SEVERITE = {
    "critique": CRITIQUE,
    "haute": HAUTE,
    "moyenne": MOYENNE,
    "technique": GRIS,
}


def _styles():
    base = getSampleStyleSheet()
    return {
        "titre": ParagraphStyle("titre", parent=base["Title"], fontName="Helvetica-Bold",
                                fontSize=20, textColor=ENCRE, spaceAfter=2, alignment=0),
        "sous_titre": ParagraphStyle("sous_titre", parent=base["Normal"], fontSize=10,
                                     textColor=GRIS, spaceAfter=14),
        "section": ParagraphStyle("section", parent=base["Heading2"], fontName="Helvetica-Bold",
                                  fontSize=12, textColor=ENCRE, spaceBefore=16, spaceAfter=8),
        "texte": ParagraphStyle("texte", parent=base["Normal"], fontSize=9.5,
                                textColor=ENCRE, leading=14),
        "note": ParagraphStyle("note", parent=base["Normal"], fontSize=8.5,
                               textColor=GRIS, leading=12),
    }


def _tableau(donnees, largeurs, en_tete=True):
    style = [
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (-1, -1), ENCRE),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, TRAIT),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
    ]
    if en_tete:
        style += [
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("TEXTCOLOR", (0, 0), (-1, 0), GRIS),
            ("FONTSIZE", (0, 0), (-1, 0), 8),
            ("LINEBELOW", (0, 0), (-1, 0), 0.8, ENCRE),
        ]
    return Table(donnees, colWidths=largeurs, style=TableStyle(style), hAlign="LEFT")


def _format_delai(secondes):
    if secondes is None:
        return "aucune alerte prise en charge"
    if secondes < 90:
        return f"{secondes} s"
    return f"{round(secondes / 60)} min"


def build_report(days: int = 7) -> bytes:
    """Construit le rapport et renvoie le PDF en mémoire."""
    st = _styles()
    fin = datetime.now()
    debut = fin - timedelta(days=days)

    summary = stats_summary()
    qualite = quality_stats(days=days)
    alertes = read_alerts(limit=5000, since_hours=days * 24)

    critiques = [a for a in alertes if a["severity"] == "critique"]
    non_traitees = [a for a in alertes if not a["acknowledged"]]
    fausses = [a for a in alertes if a["false_positive"]]

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=18 * mm, bottomMargin=18 * mm,
        title=f"SmokeWatch — rapport HSE {fin:%d/%m/%Y}",
        author="SmokeWatch",
    )

    contenu = [
        Paragraph("Rapport de surveillance HSE", st["titre"]),
        Paragraph(
            f"Période du {debut:%d/%m/%Y} au {fin:%d/%m/%Y} — "
            f"édité le {fin:%d/%m/%Y à %H:%M}", st["sous_titre"]),
        HRFlowable(width="100%", thickness=1, color=ENCRE, spaceAfter=14),
    ]

    # ── L'essentiel ──────────────────────────────────────────────────
    contenu += [
        Paragraph("L'essentiel", st["section"]),
        _tableau([
            ["Indicateur", "Valeur"],
            ["Alertes sur la période", str(len(alertes))],
            ["dont critiques", str(len(critiques))],
            ["dont non prises en charge", str(len(non_traitees))],
            ["dont déclarées fausses par les opérateurs", str(len(fausses))],
            ["Délai moyen de prise en charge",
             _format_delai(qualite["delai_prise_en_charge_s"])],
        ], [110 * mm, 60 * mm]),
    ]

    # ── Répartition par gravité ──────────────────────────────────────
    par_severite = {}
    for a in alertes:
        par_severite[a["severity"]] = par_severite.get(a["severity"], 0) + 1

    if par_severite:
        lignes = [["Gravité", "Alertes", "Part"]]
        total = len(alertes) or 1
        for severite in ("critique", "haute", "moyenne", "technique"):
            nombre = par_severite.get(severite, 0)
            if nombre:
                lignes.append([severite.capitalize(), str(nombre), f"{nombre / total * 100:.0f} %"])
        table = _tableau(lignes, [90 * mm, 40 * mm, 40 * mm])
        for i, ligne in enumerate(lignes[1:], start=1):
            couleur = COULEUR_SEVERITE.get(ligne[0].lower(), ENCRE)
            table.setStyle(TableStyle([("TEXTCOLOR", (0, i), (0, i), couleur),
                                       ("FONTNAME", (0, i), (0, i), "Helvetica-Bold")]))
        contenu += [Paragraph("Répartition par gravité", st["section"]), table]

    # ── Où ───────────────────────────────────────────────────────────
    par_camera = {}
    par_zone = {}
    for a in alertes:
        par_camera[a["camera"]] = par_camera.get(a["camera"], 0) + 1
        zone = a["zone"] or "plein cadre"
        par_zone[zone] = par_zone.get(zone, 0) + 1

    if par_camera:
        lignes = [["Caméra", "Alertes", "Fausses/jour"]]
        for camera, nombre in sorted(par_camera.items(), key=lambda kv: -kv[1]):
            faux = qualite["par_camera"].get(camera, {}).get("fausses_par_jour", 0)
            lignes.append([camera, str(nombre), f"{faux:.1f}"])
        contenu += [
            Paragraph("Où les alertes se produisent", st["section"]),
            _tableau(lignes, [90 * mm, 40 * mm, 40 * mm]),
            Spacer(1, 6),
            Paragraph(
                "Objectif du cahier des charges : moins de 2 fausses alertes par jour "
                "et par caméra. Une valeur supérieure indique des zones à retracer ou "
                "un modèle à réentraîner, pas un défaut d'exploitation.", st["note"]),
        ]

    if len(par_zone) > 1:
        lignes = [["Zone", "Alertes"]]
        for zone, nombre in sorted(par_zone.items(), key=lambda kv: -kv[1]):
            lignes.append([zone, str(nombre)])
        contenu += [Paragraph("Répartition par zone", st["section"]),
                    _tableau(lignes, [130 * mm, 40 * mm])]

    # ── Fiabilité ────────────────────────────────────────────────────
    if qualite["par_modele"]:
        lignes = [["Détection", "Alertes", "Fausses", "Taux d'erreur"]]
        for modele, stats in sorted(qualite["par_modele"].items(),
                                    key=lambda kv: -kv[1]["taux_faux"]):
            lignes.append([modele, str(stats["alertes"]), str(stats["fausses"]),
                           f"{stats['taux_faux'] * 100:.0f} %"])
        contenu += [
            Paragraph("Fiabilité des détections", st["section"]),
            _tableau(lignes, [70 * mm, 33 * mm, 33 * mm, 34 * mm]),
            Spacer(1, 6),
            Paragraph(
                "Ces taux proviennent du marquage effectué par les opérateurs. "
                "Un taux élevé sur une détection donnée désigne le modèle à corriger "
                "en priorité — et les images correspondantes constituent déjà le jeu "
                "de données de son réentraînement.", st["note"]),
        ]

    # ── Événements critiques ─────────────────────────────────────────
    if critiques:
        lignes = [["Date", "Détection", "Caméra", "Prise en charge"]]
        for a in critiques[:15]:
            horodatage = datetime.fromisoformat(a["timestamp"])
            lignes.append([
                f"{horodatage:%d/%m %H:%M}",
                a["label"],
                a["camera"] + (f" · {a['zone']}" if a["zone"] else ""),
                a["ack_by"] or "non traitée",
            ])
        table = _tableau(lignes, [28 * mm, 45 * mm, 55 * mm, 42 * mm])
        table.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "LEFT")]))
        contenu += [Paragraph("Événements critiques", st["section"]), table]
        if len(critiques) > 15:
            contenu += [Spacer(1, 6),
                        Paragraph(f"{len(critiques) - 15} autre(s) événement(s) critique(s) "
                                  "non listé(s). Export CSV pour le détail complet.",
                                  st["note"])]
    else:
        contenu += [Paragraph("Événements critiques", st["section"]),
                    Paragraph("Aucun événement critique sur la période.", st["texte"])]

    contenu += [
        Spacer(1, 18),
        HRFlowable(width="100%", thickness=0.5, color=TRAIT, spaceAfter=8),
        Paragraph(
            "Rapport produit automatiquement par SmokeWatch. Les taux de fiabilité "
            "dépendent du marquage des fausses alertes par les opérateurs : sans ce "
            "retour, ils restent incomplets.", st["note"]),
    ]

    doc.build(contenu)
    return buffer.getvalue()
