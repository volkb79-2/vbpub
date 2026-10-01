import importlib, pkgutil, dataclasses, inspect, sys, collections
import assay
mods=[]
for m in pkgutil.walk_packages(assay.__path__, 'assay.'):
    mods.append(m.name)
mods=['assay']+sorted(mods)
found=[]
for name in mods:
    mod=importlib.import_module(name)
    for attr, obj in vars(mod).items():
        if inspect.isclass(obj) and dataclasses.is_dataclass(obj) and obj.__module__==name:
            found.append((name, obj.__qualname__, obj))
    # nested classes
print(len(mods), 'modules;', len(found), 'module-level dataclasses')
for name, qn, cls in found:
    p=cls.__dataclass_params__
    fs=dataclasses.fields(cls)
    kwf={f.kw_only for f in fs}
    flags=[f.name for f in fs if (not f.init or not f.repr or not f.compare or f.hash is not None or f.kw_only is not p.kw_only)]
    if len(fs)==0 or flags or qn!=cls.__name__:
        print('NOTE', name, qn, 'nfields', len(fs), 'kwf', kwf, 'odd fields', flags)
print('attrs', [a for a in type(found[0][2].__dataclass_params__).__slots__])
# any dataclasses not reachable as module attr (nested / private defined in functions)?
