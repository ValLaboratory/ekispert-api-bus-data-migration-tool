import pytest

from ekispert_bus_data_migration import mapping
from ekispert_bus_data_migration.migrate.common import (
    StatusAmbiguous,
    StatusConverted,
    StatusFailed,
    StatusNotTarget,
)
from ekispert_bus_data_migration.migrate.station import StationInput, station

station_mapping_csv = (
    "old_code,new_code,old_name,new_name\n"
    "841234,1514600,みどり町／サンプルバス※旧,みどり町／サンプルバス\n"
    "841235,1514601,こもれび橋／サンプルバス※旧,こもれび橋／サンプルバス\n"
    "849999,1514777,こもれび橋／サンプルバス※旧,こもれび橋北／サンプルバス\n"
)


@pytest.fixture
def table():
    return mapping.parse(station_mapping_csv)


cases = [
    (
        "旧コードで変換",
        StationInput(id="s1", old_code="841234"),
        StatusConverted,
        "1514600",
        "みどり町／サンプルバス",
    ),
    (
        "旧コード不一致は名称があっても自動変換しない",
        StationInput(
            id="s2",
            old_code="999999",
            old_name="みどり町／サンプルバス※旧",
        ),
        StatusNotTarget,
        "",
        "",
    ),
    (
        "旧名称のみで変換",
        StationInput(id="s3", old_name="みどり町／サンプルバス※旧"),
        StatusConverted,
        "1514600",
        "みどり町／サンプルバス",
    ),
    (
        "同名バス停が複数ある場合は要確認",
        StationInput(id="s4", old_name="こもれび橋／サンプルバス※旧"),
        StatusAmbiguous,
        "",
        "",
    ),
    (
        "名称が対応表にない場合は対象外",
        StationInput(id="s5", old_name="存在しないバス停"),
        StatusNotTarget,
        "",
        "",
    ),
    ("入力不足はエラー", StationInput(id="s6"), StatusFailed, "", ""),
]


@pytest.mark.parametrize("name,inp,status,new_code,new_name", cases, ids=[c[0] for c in cases])
def test_station(table, name, inp, status, new_code, new_name):
    res = station(table, inp)
    assert res.status == status
    assert res.new_code == new_code
    assert res.new_name == new_name
