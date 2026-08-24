from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from pyproj import Transformer


@dataclass(frozen=True)
class LocalENU:
    origin_longitude_deg: float
    origin_latitude_deg: float
    origin_ellipsoid_height_m: float

    def __post_init__(self) -> None:
        to_ecef = Transformer.from_crs("EPSG:4979", "EPSG:4978", always_xy=True)
        to_geo = Transformer.from_crs("EPSG:4978", "EPSG:4979", always_xy=True)
        x, y, z = to_ecef.transform(
            self.origin_longitude_deg,
            self.origin_latitude_deg,
            self.origin_ellipsoid_height_m,
        )
        lon = np.deg2rad(self.origin_longitude_deg)
        lat = np.deg2rad(self.origin_latitude_deg)
        rotation = np.array(
            [
                [-np.sin(lon), np.cos(lon), 0.0],
                [-np.sin(lat) * np.cos(lon), -np.sin(lat) * np.sin(lon), np.cos(lat)],
                [np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)],
            ],
            dtype=np.float64,
        )
        object.__setattr__(self, "_to_ecef", to_ecef)
        object.__setattr__(self, "_to_geo", to_geo)
        object.__setattr__(self, "_origin_ecef", np.array([x, y, z], dtype=np.float64))
        object.__setattr__(self, "_ecef_to_enu", rotation)

    def geodetic_to_enu(
        self,
        longitude_deg: np.ndarray,
        latitude_deg: np.ndarray,
        ellipsoid_height_m: np.ndarray,
    ) -> np.ndarray:
        x, y, z = self._to_ecef.transform(longitude_deg, latitude_deg, ellipsoid_height_m)
        ecef = np.column_stack((x, y, z)).astype(np.float64, copy=False)
        return (ecef - self._origin_ecef) @ self._ecef_to_enu.T

    def enu_to_geodetic(self, points_enu: np.ndarray) -> np.ndarray:
        points = np.asarray(points_enu, dtype=np.float64)
        if points.ndim != 2 or points.shape[1] != 3:
            raise ValueError("points_enu must have shape (N, 3)")
        ecef = self._origin_ecef + points @ self._ecef_to_enu
        lon, lat, height = self._to_geo.transform(ecef[:, 0], ecef[:, 1], ecef[:, 2])
        return np.column_stack((lon, lat, height))

