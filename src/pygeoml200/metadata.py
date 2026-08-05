from __future__ import annotations

import contextlib
import copy
import logging
from dataclasses import dataclass
from importlib import resources

from dbetto import AttrsDict, TextDB
from git import GitCommandError
from legendmeta import LegendMetadata
from pygeomtools.utils import load_dict_from_config

log = logging.getLogger(__name__)

"""default metadata timestamp used to select the channel map and special-geometry metadata."""
DEFAULT_METADATA_TIMESTAMP = "20230311T235840Z"

"""enrichment assumed for detectors that have none set in the metadata."""
DUMMY_ENRICHMENT = 0.9


@dataclass(frozen=True)
class ResolvedMetadata:
    """The hardware metadata to build a geometry from.

    Hides whether the data comes from a real LEGEND metadata checkout or from the public testdata,
    see :func:`resolve_metadata`.
    """

    timestamp: str
    """Metadata timestamp the channel map and special metadata are selected for."""
    channelmap: AttrsDict
    """LEGEND-200 channel map."""

    lmeta: LegendMetadata | None
    """The LEGEND metadata checkout, or ``None`` if the public testdata is used instead."""
    public: PublicMetadataProxy | None
    """The public testdata proxy, or ``None`` if a real metadata checkout is used."""

    @property
    def is_public(self) -> bool:
        """Whether this geometry is built from public testdata only."""
        return self.public is not None

    @property
    def diodes(self) -> AttrsDict | _DiodeProxy:
        """Germanium detector metadata, indexable by detector name."""
        if self.public is not None:
            return self.public.diodes
        assert self.lmeta is not None
        return self.lmeta.hardware.detectors.germanium.diodes

    @property
    def fibers(self) -> AttrsDict | _FiberProxy:
        """Fiber module metadata, indexable by module name."""
        if self.public is not None:
            return self.public.fibers
        assert self.lmeta is not None
        return self.lmeta.hardware.detectors.lar.fibers

    def update_special_metadata(self, special_metadata: AttrsDict) -> AttrsDict:
        """Adapt the special geometry metadata to the metadata source, if necessary."""
        if self.public is None:
            return special_metadata
        return self.public.update_special_metadata(special_metadata)


def resolve_metadata(config: dict, public_geometry: bool, unavailable_msg: str) -> ResolvedMetadata:
    """Load the hardware metadata, either from a LEGEND metadata checkout or from the public testdata.

    Parameters
    ----------
    config
        the geometry configuration dictionary; ``metadata_timestamp`` and ``channelmap`` are read
        from it.
    public_geometry
        use the public testdata instead of a real metadata checkout. If this is not set and the
        checkout is unavailable, a :class:`RuntimeError` is raised. This requires user action to
        avoid accidental creation of "wrong" geometries by LEGEND members.
    unavailable_msg
        message of that :class:`RuntimeError`.
    """
    lmeta = None
    if not public_geometry:
        with contextlib.suppress(GitCommandError):
            lmeta = LegendMetadata(lazy=True)
        if lmeta is None:
            raise RuntimeError(unavailable_msg)

    public = None
    if lmeta is None:
        log.warning("CONSTRUCTING GEOMETRY FROM PUBLIC DATA ONLY")
        public = PublicMetadataProxy()

        if "metadata_timestamp" in config:
            msg = "metadata_timestamp cannot be specified for public dummy geometry"
            raise ValueError(msg)

    timestamp = config.get("metadata_timestamp", DEFAULT_METADATA_TIMESTAMP)

    def _default_channelmap() -> AttrsDict:
        if public is not None:
            return public.chmap
        assert lmeta is not None
        return lmeta.channelmap(timestamp)

    channelmap = load_dict_from_config(config, "channelmap", _default_channelmap)

    return ResolvedMetadata(timestamp=timestamp, channelmap=channelmap, lmeta=lmeta, public=public)


def fixup_enrichment(det_meta: AttrsDict, name: str) -> None:
    """Temporary fix for gedet with null enrichment value: replace it with a dummy value in-place."""
    enrichment = det_meta.production.get("enrichment")
    enrichment_val = enrichment.val if hasattr(enrichment, "val") else enrichment
    if enrichment_val is None:
        log.warning("%s has no enrichment in metadata - setting to dummy value %g!", name, DUMMY_ENRICHMENT)
        det_meta.production.enrichment = DUMMY_ENRICHMENT


class PublicMetadataProxy:
    """Provides proxies to transparently replace legend hardware metadata with sample data."""

    def __init__(self):
        dummy = TextDB(resources.files("pygeoml200") / "configs" / "dummy_geom")

        self.chmap = dummy.channelmap
        self.diodes = _DiodeProxy(dummy)
        self.fibers = _FiberProxy()

    def update_special_metadata(self, special_metadata: AttrsDict) -> AttrsDict:
        # the string is shorter because of missing special detectors.
        special_metadata = copy.deepcopy(special_metadata)
        special_metadata.hpge_string[7].minishroud_delta_length_in_mm = -200

        # mark as readonly to match real loaded metadata.
        special_metadata.__readonly__ = True
        return special_metadata


class _DiodeProxy:
    def __init__(self, dummy_detectors: TextDB):
        self.dummy_detectors = dummy_detectors

    def __getitem__(self, det_name: str) -> AttrsDict:
        # create the detector from the matching dummy metadata.
        det = self.dummy_detectors[det_name[0] + "99000A"]
        m = copy.deepcopy(det)
        m.name = det_name

        # also test the code paths with no enrichment set.
        if det_name[0] == "P":
            m.production.enrichment.val = None

        # mark as readonly to match real loaded metadata.
        m.__readonly__ = True
        return m


class _FiberProxy:
    def __getitem__(self, det_name: str) -> AttrsDict:
        m = {
            "name": det_name,
            "type": "inner" if det_name.startswith("IB") else "outer",
            "geometry": {"tpb": {"thickness_in_nm": 1000}},
        }
        # mark as readonly to match real loaded metadata.
        return AttrsDict(m, readonly=True)
