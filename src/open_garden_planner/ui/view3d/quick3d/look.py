"""A ``LookSpec`` as the properties of ``qml/Garden.qml`` (L1.4). Engine-free.

The look itself — the rig by sun elevation, the presets, ``look_for`` — is
``core/scene3d/look.py``: plain data, Qt-free. This module is the thin mapping
onto the engine's scene and decides nothing:

- ``scene_properties(look)`` — the root properties ``Quick3DHost.set_look``
  writes, by their QML names. ``COLOR_PROPERTIES`` says which of them are
  colours (the host hands those over as ``QColor``).
- ``sky_properties(sky)`` — the inputs of ONE ``ProceduralSkyTextureData``, by
  ITS property names (``Quick3DHost.set_sky`` hands them to ``buildSky``).
  ``sky_longitude`` is the measured convention: the generator draws its sun at
  compass bearing ``sunLongitude - 90°``.

What is truth is not in either mapping: the key light's DIRECTION comes from the
``SunState`` the pipeline sends (``Quick3DSink.set_sun``), and at night from the
look's stated moon (``Quick3DHost._apply_light`` — the one place that aims it).

``NEUTRAL_DAY`` (re-exported) is what a host shows before anyone hands it a
look; the scene file's defaults are its mapping, pinned by a test.
"""

from __future__ import annotations

from typing import Final

from open_garden_planner.core.scene3d.look import NEUTRAL_DAY, LookSpec, SkySpec

__all__ = ["COLOR_PROPERTIES", "NEUTRAL_DAY", "LookSpec", "SkySpec", "scene_properties",
           "sky_longitude", "sky_properties"]

#: The look properties of ``Garden.qml`` that are colours (``#rrggbb`` here).
COLOR_PROPERTIES: Final = frozenset({"sunColor", "fogColor", "skyColor", "fillColor", "meadowColor"})


def scene_properties(look: LookSpec) -> dict[str, object]:
    """``look`` as ``{QML property: value}`` — every look property of the scene file,
    and nothing else (a test pins both directions against the QML)."""
    mood, shadow, preset = look.mood, look.shadow, look.preset
    return {
        # the mood
        "sunColor": mood.sun_color,
        "sunBrightness": float(mood.sun_brightness),
        "exposure": float(mood.exposure),
        "probeExposure": float(mood.probe_exposure),
        "fogColor": look.fog_color,
        "saturation": float(mood.saturation),
        "contrast": float(mood.contrast),
        # where no sky texture lights the view
        "skyColor": look.plain_sky,
        "fillColor": look.fill_color,
        "meadowColor": look.meadow_albedo,
        # shadows
        "shadowMapQuality": shadow.map_quality,
        "shadowCascades": int(shadow.cascades),
        "softShadows": shadow.soft_shadows,
        "shadowBias": float(shadow.bias),
        "shadowFactor": float(shadow.factor),
        "pcfFactor": float(shadow.pcf_factor),
        # the preset
        "antialiasing": preset.antialiasing,
        "antialiasingQuality": preset.antialiasing_quality,
        "ambientOcclusion": bool(preset.ambient_occlusion),
        "aoSampleRate": int(preset.ao_sample_rate),
        "glow": bool(preset.glow),
        "glowHighQuality": bool(preset.glow_high_quality),
        "reflections": bool(preset.reflections),
        "fog": bool(preset.fog),
        # wind
        "windStrength": float(look.wind_strength),
    }


def sky_longitude(azimuth_deg: float) -> float:
    """Compass azimuth (clockwise from north) -> ``ProceduralSkyTextureData.sunLongitude``.

    Measured by the L0 spike's sky probe and again by the L1.4 render tier (the
    disc read 25° off-centre): with longitude L the sky draws its sun at compass
    bearing L - 90°. Re-measure after any Qt upgrade (the render tier does).
    """
    return (azimuth_deg + 90.0) % 360.0


def sky_properties(sky: SkySpec) -> dict[str, object]:
    """``sky`` as the property values of one ``ProceduralSkyTextureData``.

    ``groundHorizonColor`` is the SKY's horizon colour: the ground hemisphere
    shows in a sliver under a far-clipped meadow, and the fog colour there drew a
    dark line (``ogp-3d-renderer`` §4).
    """
    return {
        "sunLatitude": float(sky.sun_elevation_deg),
        "sunLongitude": sky_longitude(sky.sun_azimuth_deg),
        "skyTopColor": sky.top,
        "skyHorizonColor": sky.horizon,
        "groundHorizonColor": sky.horizon,
        "groundBottomColor": sky.ground_bottom,
        "sunColor": sky.sun_disc,
        "skyEnergy": float(sky.sky_energy),
        "groundEnergy": float(sky.ground_energy),
        "groundCurve": float(sky.ground_curve),
        "sunEnergy": float(sky.sun_energy),
    }
