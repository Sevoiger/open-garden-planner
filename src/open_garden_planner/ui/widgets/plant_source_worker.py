"""Background source retrieval; store mutations stay on the Qt thread."""
from PyQt6.QtCore import QObject, QRunnable, pyqtSignal

from open_garden_planner.services.plant_source_import import fetch_plant_source


def reference_photo_request(*args, **kwargs):
    """Carry an explicit image choice through a worker without stringifying it."""
    from open_garden_planner.services.plant_source_import import (
        ReferencePhotoSelectionRequired,
        fetch_reference_photo_for_plant,
    )

    try:
        return fetch_reference_photo_for_plant(*args, **kwargs)
    except ReferencePhotoSelectionRequired as choice:
        return choice


class _Signals(QObject):
    ready = pyqtSignal(object, object)


class PlantSourceWorker(QRunnable):
    def __init__(self, url):
        super().__init__()
        self.url = url
        self.signals = _Signals()

    def run(self):
        try:
            plant = fetch_plant_source(self.url)
        except Exception as error:
            from open_garden_planner.services.plant_source_errors import plant_source_error_info

            self.signals.ready.emit(None, plant_source_error_info(error, url=self.url))
        else:
            self.signals.ready.emit(plant, None)
