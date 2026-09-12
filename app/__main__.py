import logging
import sys

from .config import Config
from .server import serve
from .service import Service
from .syncthing import Syncthing, SyncthingError


def main():
    cfg = Config()
    logging.basicConfig(
        level=getattr(logging, cfg.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("syncpick")
    problems = cfg.validate()
    if problems:
        for p in problems:
            log.error(p)
        sys.exit(2)
    st = Syncthing(cfg.syncthing_url, cfg.api_key)
    try:
        version = (st.version() or {}).get("version")
        log.info("connected to Syncthing %s at %s", version, cfg.syncthing_url)
    except SyncthingError as e:
        log.warning("Syncthing not reachable yet (%s); the UI will keep retrying", e)
    if cfg.dry_run:
        log.warning("DRY_RUN is set: no patterns will be written and nothing will be deleted")
    serve(Service(cfg, st), cfg.bind, cfg.port)


if __name__ == "__main__":
    main()
