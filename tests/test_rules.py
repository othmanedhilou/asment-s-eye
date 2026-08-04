"""
Tests unitaires du moteur de regles - Ciment's Eye, Tache B.

Couvre les trois filtres successifs (ZoneFilter, TemporalConfirmer, CooldownManager)
et le pipeline complet (RuleEngine). Voir docs/TACHE_B_REGLES_ALERTES.md.
"""

from datetime import datetime, timedelta

from app.rules import (
    CooldownManager,
    Detection,
    RuleEngine,
    TemporalConfirmer,
    ZoneFilter,
)

# ---------------------------------------------------------------------------
# Fixtures communes
# ---------------------------------------------------------------------------

CAM = "CAM-CONVOYEUR-01"
T0 = datetime(2026, 1, 1, 8, 0, 0)


def make_detection(
    camera_id: str = CAM,
    timestamp: datetime = T0,
    class_name: str = "no_helmet",
    confidence: float = 0.9,
    bbox: tuple[int, int, int, int] = (900, 500, 1000, 600),
    frame_index: int = 0,
) -> Detection:
    """Construit une Detection de test avec des valeurs par defaut sensees."""
    return Detection(
        camera_id=camera_id,
        timestamp=timestamp,
        class_name=class_name,
        confidence=confidence,
        bbox=bbox,
        frame_index=frame_index,
    )


ZONES_CONFIG = {
    CAM: {
        "zone_convoyeur": {
            "polygon": [[0.1, 0.2], [0.8, 0.2], [0.8, 0.9], [0.1, 0.9]],
            "use_cases": ["UC-01", "UC-03", "UC-04", "UC-09", "UC-14"],
            "label": "convoyeur",
        },
        "zone_interdite": {
            "polygon": [[0.3, 0.1], [0.6, 0.1], [0.6, 0.5], [0.3, 0.5]],
            "use_cases": ["UC-14"],
            "label": "acces_four",
        },
    }
}

IMAGE_DIMENSIONS = {CAM: (1000, 1000)}


# ---------------------------------------------------------------------------
# TestZoneFilter
# ---------------------------------------------------------------------------


class TestZoneFilter:
    def test_detection_inside_zone(self):
        """Une detection dont le centre est dans la zone doit etre acceptee."""
        zone_filter = ZoneFilter(ZONES_CONFIG)
        # centre en (0.5, 0.5) normalise -> dans zone_convoyeur
        detection = make_detection(bbox=(450, 450, 550, 550))

        matched = zone_filter.is_in_zone(detection, 1000, 1000)

        zone_names = {z["zone_name"] for z in matched}
        assert "zone_convoyeur" in zone_names

    def test_detection_outside_zone(self):
        """Une detection hors de toute zone doit etre rejetee."""
        zone_filter = ZoneFilter(ZONES_CONFIG)
        # centre en (0.95, 0.95) -> hors des deux polygones
        detection = make_detection(bbox=(940, 940, 960, 960))

        matched = zone_filter.is_in_zone(detection, 1000, 1000)

        assert matched == []

    def test_detection_on_boundary(self):
        """Une detection dont le centre tombe exactement sur le bord du polygone est acceptee."""
        zone_filter = ZoneFilter(ZONES_CONFIG)
        # centre en (0.1, 0.5) normalise -> exactement sur le bord gauche de zone_convoyeur
        detection = make_detection(bbox=(100, 500, 100, 500))

        matched = zone_filter.is_in_zone(detection, 1000, 1000)

        zone_names = {z["zone_name"] for z in matched}
        assert "zone_convoyeur" in zone_names

    def test_detection_unknown_camera(self):
        """Une camera absente de la configuration ne doit renvoyer aucune zone (pas d'exception)."""
        zone_filter = ZoneFilter(ZONES_CONFIG)
        detection = make_detection(camera_id="CAM-INCONNUE", bbox=(450, 450, 550, 550))

        matched = zone_filter.is_in_zone(detection, 1000, 1000)

        assert matched == []

    def test_detection_matches_multiple_zones(self):
        """Une detection dans la zone de recouvrement des deux polygones doit renvoyer les deux."""
        zone_filter = ZoneFilter(ZONES_CONFIG)
        # centre en (0.45, 0.3) normalise -> dans zone_convoyeur ET zone_interdite
        detection = make_detection(bbox=(440, 290, 460, 310))

        matched = zone_filter.is_in_zone(detection, 1000, 1000)

        zone_names = {z["zone_name"] for z in matched}
        assert zone_names == {"zone_convoyeur", "zone_interdite"}


# ---------------------------------------------------------------------------
# TestTemporalConfirmer
# ---------------------------------------------------------------------------


class TestTemporalConfirmer:
    def test_single_detection_not_confirmed(self):
        """Une seule detection ne doit pas confirmer (delai UC-01 = 3s)."""
        confirmer = TemporalConfirmer()
        detection = make_detection(class_name="no_helmet", timestamp=T0)

        confirmed = confirmer.update(detection, "convoyeur", "UC-01")

        assert confirmed is False

    def test_sustained_detection_confirmed(self):
        """Des detections soutenues au-dela du delai doivent finir par confirmer."""
        confirmer = TemporalConfirmer()

        d1 = make_detection(timestamp=T0)
        assert confirmer.update(d1, "convoyeur", "UC-01") is False

        d2 = make_detection(timestamp=T0 + timedelta(seconds=1.5))
        assert confirmer.update(d2, "convoyeur", "UC-01") is False

        d3 = make_detection(timestamp=T0 + timedelta(seconds=3.1))
        assert confirmer.update(d3, "convoyeur", "UC-01") is True

    def test_confirmation_happens_only_once(self):
        """Une fois confirmee, la detection ne doit pas re-declencher a chaque frame suivante."""
        confirmer = TemporalConfirmer()
        # UC-03 (feu) : delai de 1s, gap de 1.1s < GAP_RESET_SECONDS (2s) donc pas de reset
        confirmer.update(make_detection(timestamp=T0), "four", "UC-03")
        confirmed = confirmer.update(
            make_detection(timestamp=T0 + timedelta(seconds=1.1)), "four", "UC-03"
        )
        assert confirmed is True

        # frame suivante, toujours dans la meme sequence : ne doit plus retourner True
        again = confirmer.update(
            make_detection(timestamp=T0 + timedelta(seconds=1.5)), "four", "UC-03"
        )
        assert again is False

    def test_gap_resets_counter(self):
        """Un ecart de plus de 2s entre deux detections doit reinitialiser le compteur."""
        confirmer = TemporalConfirmer()

        d1 = make_detection(timestamp=T0)
        confirmer.update(d1, "convoyeur", "UC-01")

        # gap de 5s > GAP_RESET_SECONDS (2s) -> repart de zero au lieu de cumuler
        d2 = make_detection(timestamp=T0 + timedelta(seconds=5))
        confirmed = confirmer.update(d2, "convoyeur", "UC-01")
        assert confirmed is False

        # frame suivante proche (gap < 2s) : le compteur continue de s'accumuler depuis d2
        d3 = make_detection(timestamp=T0 + timedelta(seconds=6.5))
        confirmed = confirmer.update(d3, "convoyeur", "UC-01")
        assert confirmed is False

        # 3.1s apres le nouveau depart (d2) : le delai UC-01 (3s) est atteint
        d4 = make_detection(timestamp=T0 + timedelta(seconds=8.1))
        confirmed = confirmer.update(d4, "convoyeur", "UC-01")
        assert confirmed is True

    def test_different_delays_per_uc(self):
        """Chaque use case a son propre delai de confirmation."""
        confirmer = TemporalConfirmer()

        # UC-03 (feu) : delai de 1s, doit deja confirmer a 1.1s
        d_fire_1 = make_detection(class_name="fire", timestamp=T0)
        confirmer.update(d_fire_1, "four", "UC-03")
        d_fire_2 = make_detection(class_name="fire", timestamp=T0 + timedelta(seconds=1.1))
        assert confirmer.update(d_fire_2, "four", "UC-03") is True

        # UC-02 (chute) : delai de 10s, ne doit pas encore confirmer a 3s
        d_fall_1 = make_detection(class_name="fallen_person", timestamp=T0)
        confirmer.update(d_fall_1, "convoyeur", "UC-02")
        d_fall_2 = make_detection(
            class_name="fallen_person", timestamp=T0 + timedelta(seconds=3)
        )
        assert confirmer.update(d_fall_2, "convoyeur", "UC-02") is False

    def test_independent_keys_do_not_interfere(self):
        """Deux cameras (ou zones/UC) differentes ont des etats independants."""
        confirmer = TemporalConfirmer()
        confirmer.update(make_detection(camera_id="CAM-A", timestamp=T0), "z", "UC-01")
        # une seule detection sur CAM-B : ne doit pas etre confirmee malgre l'etat de CAM-A
        confirmed = confirmer.update(
            make_detection(camera_id="CAM-B", timestamp=T0), "z", "UC-01"
        )
        assert confirmed is False

    def test_reset_clears_state(self):
        """reset() doit supprimer l'etat existant, un nouveau cycle repart de zero."""
        confirmer = TemporalConfirmer()
        confirmer.update(make_detection(timestamp=T0), "convoyeur", "UC-01")

        confirmer.reset(CAM, "convoyeur", "UC-01")

        # apres reset, meme un timestamp tres avance ne doit pas confirmer immediatement
        confirmed = confirmer.update(
            make_detection(timestamp=T0 + timedelta(seconds=3.1)), "convoyeur", "UC-01"
        )
        assert confirmed is False


# ---------------------------------------------------------------------------
# TestCooldown
# ---------------------------------------------------------------------------


class TestCooldown:
    def test_no_cooldown_before_first_alert(self):
        """Sans alerte prealable, il n'y a pas de cooldown actif."""
        cooldown = CooldownManager()
        assert cooldown.is_in_cooldown(CAM, "convoyeur", "UC-01", T0) is False

    def test_alert_during_cooldown_blocked(self):
        """Une alerte identique juste apres l'enregistrement doit etre bloquee (UC-01 = 60s)."""
        cooldown = CooldownManager()
        cooldown.register_alert(CAM, "convoyeur", "UC-01", T0)

        blocked = cooldown.is_in_cooldown(
            CAM, "convoyeur", "UC-01", T0 + timedelta(seconds=30)
        )

        assert blocked is True

    def test_alert_after_cooldown_allowed(self):
        """Une alerte est de nouveau autorisee une fois le cooldown expire."""
        cooldown = CooldownManager()
        cooldown.register_alert(CAM, "convoyeur", "UC-01", T0)

        allowed = cooldown.is_in_cooldown(
            CAM, "convoyeur", "UC-01", T0 + timedelta(seconds=61)
        )

        assert allowed is False

    def test_cooldown_duration_depends_on_use_case(self):
        """UC-03 (feu) a un cooldown de 30s, plus court que UC-01 (60s)."""
        cooldown = CooldownManager()
        cooldown.register_alert(CAM, "four", "UC-03", T0)

        # a 31s, le cooldown UC-03 (30s) doit avoir expire
        assert cooldown.is_in_cooldown(CAM, "four", "UC-03", T0 + timedelta(seconds=31)) is False

    def test_cooldown_scoped_per_key(self):
        """Le cooldown est independant par (camera, zone, use_case)."""
        cooldown = CooldownManager()
        cooldown.register_alert(CAM, "convoyeur", "UC-01", T0)

        # meme camera, zone differente : pas de cooldown
        assert cooldown.is_in_cooldown(CAM, "acces_four", "UC-01", T0) is False
        # meme camera et zone, UC different : pas de cooldown
        assert cooldown.is_in_cooldown(CAM, "convoyeur", "UC-04", T0) is False


# ---------------------------------------------------------------------------
# TestRuleEngine
# ---------------------------------------------------------------------------


class TestRuleEngine:
    def _engine(self) -> RuleEngine:
        return RuleEngine(ZONES_CONFIG, IMAGE_DIMENSIONS)

    def test_single_detection_produces_no_alert(self):
        """Une detection isolee ne doit pas encore produire d'alerte (confirmation non atteinte)."""
        engine = self._engine()
        detection = make_detection(class_name="no_helmet", bbox=(450, 450, 550, 550), timestamp=T0)

        alerts = engine.process(detection)

        assert alerts == []

    def test_full_pipeline_fire(self):
        """Feu confirme dans une zone valide doit produire une alerte CRITIQUE (UC-03, delai 1s)."""
        engine = self._engine()

        d1 = make_detection(class_name="fire", bbox=(450, 450, 550, 550), timestamp=T0)
        assert engine.process(d1) == []

        d2 = make_detection(
            class_name="fire", bbox=(450, 450, 550, 550), timestamp=T0 + timedelta(seconds=1.1)
        )
        alerts = engine.process(d2)

        assert len(alerts) == 1
        alert = alerts[0]
        assert alert.use_case == "UC-03"
        assert alert.alert_type == "FIRE"
        assert alert.priority == "CRITIQUE"
        assert alert.camera_id == CAM
        assert alert.zone == "convoyeur"

    def test_full_pipeline_false_positive_outside_zone(self):
        """Une detection valide mais hors de toute zone ne doit jamais produire d'alerte."""
        engine = self._engine()
        # centre hors des deux polygones
        outside = make_detection(class_name="fire", bbox=(940, 940, 960, 960), timestamp=T0)

        alerts = engine.process(outside)
        alerts += engine.process(
            make_detection(class_name="fire", bbox=(940, 940, 960, 960), timestamp=T0 + timedelta(seconds=5))
        )

        assert alerts == []

    def test_unknown_class_produces_no_alert(self):
        """Une classe de detection non mappee a un use case ne doit jamais alerter."""
        engine = self._engine()
        detection = make_detection(class_name="dust_cloud", bbox=(450, 450, 550, 550), timestamp=T0)

        alerts = engine.process(detection)

        assert alerts == []

    def test_cooldown_blocks_repeated_alert(self):
        """Apres une premiere alerte confirmee, une nouvelle sequence dans le cooldown ne doit rien produire."""
        engine = self._engine()

        # confirme une premiere alerte UC-03 (delai 1s, cooldown 30s)
        engine.process(make_detection(class_name="fire", bbox=(450, 450, 550, 550), timestamp=T0))
        first_alerts = engine.process(
            make_detection(class_name="fire", bbox=(450, 450, 550, 550), timestamp=T0 + timedelta(seconds=1.1))
        )
        assert len(first_alerts) == 1

        # nouvelle sequence de detections 5s plus tard, toujours dans le cooldown de 30s
        t2 = T0 + timedelta(seconds=6)
        engine.process(make_detection(class_name="fire", bbox=(450, 450, 550, 550), timestamp=t2))
        second_alerts = engine.process(
            make_detection(class_name="fire", bbox=(450, 450, 550, 550), timestamp=t2 + timedelta(seconds=1.1))
        )

        assert second_alerts == []

    def test_alert_allowed_again_after_cooldown_expires(self):
        """Une fois le cooldown expire, une nouvelle sequence confirmee doit re-alerter."""
        engine = self._engine()

        engine.process(make_detection(class_name="fire", bbox=(450, 450, 550, 550), timestamp=T0))
        first_alerts = engine.process(
            make_detection(class_name="fire", bbox=(450, 450, 550, 550), timestamp=T0 + timedelta(seconds=1.1))
        )
        assert len(first_alerts) == 1

        # 35s plus tard : cooldown UC-03 (30s) expire, nouvelle sequence de confirmation
        t2 = T0 + timedelta(seconds=35)
        engine.process(make_detection(class_name="fire", bbox=(450, 450, 550, 550), timestamp=t2))
        second_alerts = engine.process(
            make_detection(class_name="fire", bbox=(450, 450, 550, 550), timestamp=t2 + timedelta(seconds=1.1))
        )

        assert len(second_alerts) == 1

    def test_two_simultaneous_alerts_on_different_cameras(self):
        """Deux cameras independantes generant chacune une alerte doivent toutes deux etre traitees."""
        zones_config = {**ZONES_CONFIG, "CAM-FOUR-01": ZONES_CONFIG[CAM]}
        image_dims = {**IMAGE_DIMENSIONS, "CAM-FOUR-01": (1000, 1000)}
        engine = RuleEngine(zones_config, image_dims)

        engine.process(
            make_detection(camera_id=CAM, class_name="fire", bbox=(450, 450, 550, 550), timestamp=T0)
        )
        alerts_cam1 = engine.process(
            make_detection(
                camera_id=CAM, class_name="fire", bbox=(450, 450, 550, 550),
                timestamp=T0 + timedelta(seconds=1.1),
            )
        )

        engine.process(
            make_detection(camera_id="CAM-FOUR-01", class_name="fire", bbox=(450, 450, 550, 550), timestamp=T0)
        )
        alerts_cam2 = engine.process(
            make_detection(
                camera_id="CAM-FOUR-01", class_name="fire", bbox=(450, 450, 550, 550),
                timestamp=T0 + timedelta(seconds=1.1),
            )
        )

        assert len(alerts_cam1) == 1
        assert len(alerts_cam2) == 1
        assert alerts_cam1[0].camera_id == CAM
        assert alerts_cam2[0].camera_id == "CAM-FOUR-01"

    def test_detection_can_trigger_multiple_use_cases(self):
        """Une classe mappee a plusieurs UC (ex: person -> UC-09 et UC-14) peut produire plusieurs alertes."""
        engine = self._engine()
        # centre en zone de recouvrement (convoyeur + acces_four)
        bbox = (440, 290, 460, 310)

        all_alerts = []
        # frames rapprochees (< 2s) pour accumuler jusqu'au delai le plus long (UC-09 : 5s)
        for offset in (0, 1.8, 3.6, 5.1):
            all_alerts += engine.process(
                make_detection(
                    class_name="person", bbox=bbox, timestamp=T0 + timedelta(seconds=offset)
                )
            )

        use_cases = {a.use_case for a in all_alerts}
        # UC-09 (surpopulation, delai 5s) et UC-14 (intrusion, delai 1s) sont tous deux confirmes
        assert "UC-09" in use_cases
        assert "UC-14" in use_cases

    def test_alert_metadata_contains_source_detection_info(self):
        """Les metadonnees de l'alerte doivent tracer la detection d'origine."""
        engine = self._engine()
        bbox = (450, 450, 550, 550)

        engine.process(make_detection(class_name="fire", bbox=bbox, timestamp=T0))
        alerts = engine.process(
            make_detection(class_name="fire", bbox=bbox, timestamp=T0 + timedelta(seconds=1.1))
        )

        assert alerts[0].metadata["class_name"] == "fire"
        assert alerts[0].metadata["bbox"] == bbox
        assert alerts[0].metadata["zone_name"] == "zone_convoyeur"
