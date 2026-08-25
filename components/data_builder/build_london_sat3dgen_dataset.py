from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parent
DATA_ROOT = ROOT / 'london_vigor_root'
CITY = 'London'
CITY_DIR = DATA_ROOT / CITY

PANORAMA_SRC = ROOT / 'panorama'
SATELLITE_SRC = ROOT / 'satellite'
DSM_SRC = ROOT / 'LIDAR_Composite_1m_First_Return_DSM_2022_extents.json'

TRAIN_SRC = ROOT / 'london_train.txt'
VAL_SRC = ROOT / 'london_val.txt'
TEST_SRC = ROOT / 'london_test.txt'


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def copy_images(src_dir: Path, dst_dir: Path, suffix: str) -> int:
    ensure_dir(dst_dir)
    count = 0
    for src_path in sorted(src_dir.glob(f'*{suffix}')):
        dst_path = dst_dir / src_path.name
        if not dst_path.exists():
            shutil.copy2(src_path, dst_path)
        count += 1
    return count


def read_split(path: Path) -> str:
    return path.read_text(encoding='utf-8').strip() + '\n'


def write_split(path: Path, content: str) -> None:
    path.write_text(content, encoding='utf-8')


if __name__ == '__main__':
    ensure_dir(DATA_ROOT)
    ensure_dir(CITY_DIR)
    ensure_dir(CITY_DIR / 'panorama')
    ensure_dir(CITY_DIR / 'satellite')
    ensure_dir(CITY_DIR / 'pano_sky_mask')
    ensure_dir(CITY_DIR / 'sat_depth')
    ensure_dir(DATA_ROOT / 'London_DSM')

    pano_count = copy_images(PANORAMA_SRC, CITY_DIR / 'panorama', '.jpg')
    sat_count = copy_images(SATELLITE_SRC, CITY_DIR / 'satellite', '.png')

    if DSM_SRC.exists():
        shutil.copy2(DSM_SRC, DATA_ROOT / 'London_DSM' / DSM_SRC.name)

    train_content = read_split(TRAIN_SRC)
    val_content = read_split(VAL_SRC)
    test_content = read_split(TEST_SRC)

    write_split(DATA_ROOT / 'train.txt', train_content)
    write_split(DATA_ROOT / 'train__london.txt', train_content)
    write_split(DATA_ROOT / 'train__corrected_all_3city_remove_building.txt', train_content)

    write_split(DATA_ROOT / 'val.txt', val_content)
    write_split(DATA_ROOT / 'val__london.txt', val_content)

    write_split(DATA_ROOT / 'test.txt', test_content)
    write_split(DATA_ROOT / 'test__london.txt', test_content)
    write_split(DATA_ROOT / 'test_remove_building.txt', test_content)

    print(f'Created dataset root: {DATA_ROOT}')
    print(f'City folder: {CITY_DIR}')
    print(f'Copied {pano_count} panorama images and {sat_count} satellite images')
    print(f'Placed DSM at: {DATA_ROOT / "London_DSM" / DSM_SRC.name}')
    print('Split files written:')
    for p in [
        DATA_ROOT / 'train__corrected_all_3city_remove_building.txt',
        DATA_ROOT / 'test_remove_building.txt',
        DATA_ROOT / 'train.txt',
        DATA_ROOT / 'val.txt',
        DATA_ROOT / 'test.txt',
    ]:
        print(p)
