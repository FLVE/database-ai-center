from pathlib import Path

import yaml

from dbic.core.models import Instance


class YamlRegistry:
    def __init__(self, path: Path) -> None:
        data = yaml.safe_load(path.read_text())
        instances = [Instance.model_validate(item) for item in data["instances"]]
        if len({item.id for item in instances}) != len(instances):
            raise ValueError("duplicate instance IDs")
        self.instances = {item.id: item for item in instances}

    async def get_instance(self, instance_id: str) -> Instance | None:
        return self.instances.get(instance_id)
