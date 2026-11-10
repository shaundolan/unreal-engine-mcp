# Create or edit a DataAsset / PrimaryDataAsset instance and set its properties.
# ARGS: path, class (a DataAsset subclass: native name, /Script path, or a Blueprint class path; needed to create),
#       properties {name: value} (snake_case or the property's display name)
# ruff: noqa: F821
_path = ARGS["path"]
_eal = unreal.EditorAssetLibrary
_asset = unreal.load_asset(_path) if _eal.does_asset_exist(_path) else None
_created = False
if _asset is None:
    _cls = resolve_class(ARGS.get("class") or "")
    if _cls is None:
        raise LookupError(f"data asset class {ARGS.get('class')!r} not found (a DataAsset or PrimaryDataAsset subclass)")
    _factory = unreal.DataAssetFactory()
    _factory.set_editor_property("data_asset_class", _cls)
    _pkg, _name = _path.rsplit("/", 1)
    _asset = unreal.AssetToolsHelpers.get_asset_tools().create_asset(_name.split(".")[0], _pkg, None, _factory)
    if _asset is None:
        raise RuntimeError(f"could not create a {ARGS.get('class')} at {_path}")
    _created = True
elif not isinstance(_asset, unreal.DataAsset):
    raise TypeError(f"{_path} is a {_asset.get_class().get_name()}, not a DataAsset")


def _snake(n):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", n.replace(" ", "")).lower()


_set, _errors = [], []
for _k, _v in (ARGS.get("properties") or {}).items():
    for _cand in (_k, _snake(_k)):
        try:
            _cur = _asset.get_editor_property(_cand)
        except Exception:
            continue
        try:
            _asset.set_editor_property(_cand, coerce(_cur, _v))
            _set.append(_k)
        except Exception as _e:
            _errors.append(f"{_k}: {_e}")
        break
    else:
        _errors.append(f"{_k}: no such property on {_asset.get_class().get_name()}")
_eal.save_loaded_asset(_asset, only_if_is_dirty=False)
result = {"path": _asset.get_path_name(), "class": _asset.get_class().get_name(), "created": _created, "set": _set}
if _errors:
    result.update(success=False, error="; ".join(_errors))
