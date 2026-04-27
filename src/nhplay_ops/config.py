from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class SqlConfig:
    server: str
    database: str
    username: str
    password: str
    app_name: str = "NHPlayPythonOps"
    driver: str = "ODBC Driver 18 for SQL Server"
    trust_server_certificate: bool = True
    mars_connection: bool = True

    @classmethod
    def from_env(cls) -> "SqlConfig":
        return cls(
            server=os.environ["GWS_SQL_SERVER"],
            database=os.environ["GWS_SQL_DATABASE"],
            username=os.environ["GWS_SQL_USER"],
            password=os.environ["GWS_SQL_PASSWORD"],
            app_name=os.environ.get("GWS_SQL_APP_NAME", "NHPlayPythonOps"),
            driver=os.environ.get("GWS_SQL_DRIVER", "ODBC Driver 18 for SQL Server"),
            trust_server_certificate=os.environ.get("GWS_SQL_TRUST_SERVER_CERTIFICATE", "1") not in {"0", "false", "False"},
            mars_connection=os.environ.get("GWS_SQL_MARS_CONNECTION", "1") not in {"0", "false", "False"},
        )

    def connection_string(self) -> str:
        parts = [
            f"DRIVER={{{self.driver}}}",
            f"SERVER={self.server}",
            f"DATABASE={self.database}",
            f"UID={self.username}",
            f"PWD={self.password}",
            f"APP={self.app_name}",
        ]
        if self.trust_server_certificate:
            parts.append("TrustServerCertificate=yes")
        if self.mars_connection:
            parts.append("MARS_Connection=yes")
        return ";".join(parts)


def load_env_file(path: str | None) -> None:
    if not path:
        return

    env_path = Path(path)
    if not env_path.exists():
        raise FileNotFoundError(f"Env file not found: {env_path}")

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        os.environ[key.strip()] = value

