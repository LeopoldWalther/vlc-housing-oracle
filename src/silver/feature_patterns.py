"""The registry of feature wordings: which column or label carries what a wording says.

A wording is a feature string of the portal with every number replaced by ``#``, trimmed and
lower-cased (``feature_wording`` in ``sql/feature_wording.sql``). Every wording seen in
``listing_text.features`` belongs here with its target: the ``listing_detail`` column that
carries its meaning, one of ``LABEL_TARGETS``, or ``UNREAD`` when no column carries it yet.

To add a wording, read the rows of ``feature_pattern`` with a null target and add each to
``KNOWN_PATTERNS`` with its target. A wording that the leak guard refuses is never written in
clear: list the SHA-256 of its UTF-8 bytes in ``KNOWN_PATTERN_DIGESTS`` instead. A new wording
can be typed text, so check that it is the portal's wording before it is committed.
"""

import hashlib
from pathlib import Path

import duckdb
import pyarrow as pa

LABEL_TARGETS = ("is_tenant_occupied", "is_bare_ownership", "furnishing")

# The target of a wording whose meaning no column and no label carries yet.
UNREAD = "unread"

KNOWN_PATTERNS = {
    "# habitaciones": "rooms",
    "segunda mano/buen estado": "condition",
    "construido en #": "year_built",
    "#ª planta exterior": "floor",
    "aire acondicionado": "has_ac_state",
    "con ascensor": "has_elevator_state",
    "# baños": "bathrooms",
    "armarios empotrados": "has_fitted_wardrobes_state",
    "# m² construidos": "sqm_built",
    "consumo: # kwh/m² año": "energy_consumption_kwh_m2",
    "# m² construidos, # m² útiles": "sqm_built",
    "emisiones: # kg co#/m² año": "energy_emissions_kg_m2",
    "# baño": "bathrooms",
    "consumo:": "energy_consumption_rating",
    "plaza de garaje incluida en el precio": "parking_included_in_price",
    "balcón": "has_balcony_state",
    "terraza": "has_terrace_state",
    "trastero": "has_storage_room_state",
    "sin ascensor": "has_elevator_state",
    "no dispone de calefacción": "has_heating",
    "emisiones:": "energy_emissions_rating",
    "amueblado y cocina equipada": "furnishing",
    "piscina": "has_pool_state",
    "terraza y balcón": "has_terrace_state",
    "jardín": "has_garden_state",
    "parcela de # m²": "sqm_plot",
    "casa o chalet independiente": "property_type",
    "calefacción individual: eléctrica": "heating_type",
    "orientación este": "orientation",
    "segunda mano/para reformar": "condition",
    "# plantas": "storeys",
    "solo acceso exterior adaptado para personas con movilidad reducida": "unread",
    "# habitación": "rooms",
    "calefacción individual": "heating_scope",
    "calefacción individual: bomba de frío/calor": "heating_type",
    "orientación sur": "orientation",
    "calefacción individual: gas natural": "heating_type",
    "bajo exterior": "floor",
    "orientación sur, este": "orientation",
    "promoción de obra nueva": "condition",
    "calefacción central": "heating_scope",
    "orientación este, oeste": "orientation",
    "alquilada, con inquilinos": "is_tenant_occupied",
    "orientación oeste": "orientation",
    "acceso y vivienda adaptados para personas con movilidad reducida": "unread",
    "orientación norte": "orientation",
    "cocina equipada y casa sin amueblar": "furnishing",
    "chalet adosado": "property_type",
    "exterior": "is_exterior",
    "#ª planta interior": "floor",
    "sin habitación": "rooms",
    "orientación norte, sur, este, oeste": "orientation",
    "orientación norte, este": "orientation",
    "calefacción central: gas": "heating_type",
    "# planta": "storeys",
    "orientación norte, sur": "orientation",
    "plaza de garaje por # € adicionales": "unread",
    "orientación sur, oeste": "orientation",
    "en trámite": "unread",
    "#ª planta": "floor",
    "cocina sin equipar y casa sin amueblar": "furnishing",
    "orientación norte, oeste": "orientation",
    "chalet pareado": "property_type",
    "casa o chalet": "property_type",
    "solo interior de la vivienda adaptado para personas con movilidad reducida": "unread",
    "ocupada ilegalmente qué se considera una vivienda ocupada": "is_occupied",
    "orientación sur, este, oeste": "orientation",
    "plaza de garaje por # €/mes adicionales": "unread",
    "no indicado": "unread",
    "bajo interior": "floor",
    "entreplanta exterior": "floor",
    "sin baños": "bathrooms",
    "casa de pueblo": "property_type",
    "orientación norte, sur, este": "orientation",
    "calefacción central: gasoil": "heating_type",
    "orientación norte, este, oeste": "orientation",
    "nuda propiedad qué es la nuda propiedad": "is_bare_ownership",
    "finca rústica": "property_type",
    "calefacción individual: gas propano/butano": "heating_type",
    "chimenea": "unread",
    "orientación norte, sur, oeste": "orientation",
    "interior": "is_exterior",
    "bajo": "floor",
    "casa rural": "property_type",
    "masía": "property_type",
    "entreplanta interior": "floor",
    "caserón": "property_type",
    "planta -# exterior": "floor",
    "semi-sótano exterior": "floor",
    "casa terrera": "property_type",
    "entreplanta": "floor",
    "casa rústica": "property_type",
    "no dispone de calefacción: eléctrica": "has_heating",
    "semi-sótano interior": "floor",
    "segunda mano/": "condition",
    "calefacción individual: gas": "heating_type",
    "cortijo": "property_type",
    "sótano exterior": "floor",
    "planta -# interior": "floor",
}

# Wordings listed by the SHA-256 of their UTF-8 bytes, because the leak guard refuses one of
# their words. The one entry says that the property is exempt from the energy certificate.
KNOWN_PATTERN_DIGESTS = {
    "b2c7da2bbba335a16fc4dca108d834f6ce1485738f233cad6ff526e89c6cd3e9": "unread",
}

FURNISHING_VALUES = {
    "amueblado y cocina equipada": "furnished",
    "cocina equipada y casa sin amueblar": "kitchen_only",
    "cocina sin equipar y casa sin amueblar": "unfurnished",
}

_WORDING_MACRO = Path(__file__).resolve().parent / "sql" / "feature_wording.sql"


def registry_rows() -> pa.Table:
    """One row per registered wording: its digest, its target and its label value.

    ``label_value`` is the furnishing value of a furnishing wording and null for every other row.
    """
    rows = [
        (
            hashlib.sha256(pattern.encode("utf-8")).hexdigest(),
            target,
            FURNISHING_VALUES.get(pattern),
        )
        for pattern, target in KNOWN_PATTERNS.items()
    ]
    rows += [(digest, target, None) for digest, target in KNOWN_PATTERN_DIGESTS.items()]
    return pa.table(
        {
            "pattern_sha256": pa.array([row[0] for row in rows], pa.string()),
            "target": pa.array([row[1] for row in rows], pa.string()),
            "label_value": pa.array([row[2] for row in rows], pa.string()),
        }
    )


def register_feature_registry(connection: duckdb.DuckDBPyConnection) -> None:
    """Define ``feature_wording`` and replace the temporary table ``feature_registry``."""
    connection.execute(_WORDING_MACRO.read_text(encoding="utf-8"))
    connection.register("feature_registry_rows", registry_rows())
    connection.execute(
        "CREATE OR REPLACE TEMP TABLE feature_registry AS FROM feature_registry_rows"
    )
    connection.unregister("feature_registry_rows")
