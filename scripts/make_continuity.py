"""Generate the original, nonempty two-terminal KiCad test design."""
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5
import argparse

NAME = "continuity"
SYMBOL = r'''(pin_numbers hide)
(pin_names (offset 0) hide)
(exclude_from_sim no) (in_bom yes) (on_board yes)
(property "Reference" "J" (at 0 2.54 0) (effects (font (size 1.27 1.27))))
(property "Value" "Terminal" (at 0 -2.54 0) (effects (font (size 1.27 1.27))))
(property "Footprint" "EHE:Terminal" (at 0 0 0) (effects (font (size 1.27 1.27)) hide))
(symbol "Terminal_0_1"
    (rectangle (start -0.635 0.635) (end 0.635 -0.635)
        (stroke (width 0.254) (type default)) (fill (type background))))
(symbol "Terminal_1_1"
    (pin passive line (at -2.54 0 0) (length 1.905)
        (name "1" (effects (font (size 1.27 1.27))))
        (number "1" (effects (font (size 1.27 1.27))))))'''


def uid(key):
    return str(uuid5(NAMESPACE_URL, "urn:ehe:continuity:" + key))


def symbol(ref, x, rotation):
    return f'''(symbol (lib_id "EHE:Terminal") (at {x} 50.8 {rotation}) (unit 1)
        (exclude_from_sim no) (in_bom yes) (on_board yes) (dnp no)
        (uuid "{uid(ref)}")
        (property "Reference" "{ref}" (at {x} 47.625 0) (effects (font (size 1.27 1.27))))
        (property "Value" "Terminal" (at {x} 53.975 0) (effects (font (size 1.27 1.27))))
        (property "Footprint" "EHE:Terminal" (at {x} 50.8 0) (effects (font (size 1.27 1.27)) hide))
        (pin "1" (uuid "{uid(ref + '-pin')}"))
        (instances (project "{NAME}" (path "/{uid(NAME)}" (reference "{ref}") (unit 1)))))'''


def footprint(name, ref="REF**", x=None):
    board = x is not None
    placed = f'(at {x} 30) (uuid "{uid(ref + "-footprint")}")' if board else ""
    pad = f'(net 1 "LINK") (uuid "{uid(ref + "-pad")}")' if board else ""
    path = f'(path "/{uid(NAME)}/{uid(ref)}")' if board else ""
    return f'''(footprint "{name}" (layer "F.Cu") {placed} {path}
        (property "Reference" "{ref}" (at 0 -2.25 0) (layer "F.SilkS") (effects (font (size 0.8 0.8) (thickness 0.15))))
        (property "Value" "Terminal" (at 0 2.25 0) (layer "F.Fab") (effects (font (size 0.8 0.8) (thickness 0.15))))
        (attr through_hole)
        (fp_rect (start -1.1 -1.1) (end 1.1 1.1) (stroke (width 0.05) (type default))
            (fill none) (layer "F.CrtYd") (uuid "{uid(ref + '-courtyard')}"))
        (pad "1" thru_hole circle (at 0 0) (size 1.5 1.5) (drill 0.75)
            (layers "*.Cu" "*.Mask") {pad}))'''


def build(root):
    root.mkdir(parents=True, exist_ok=True)
    (root / "EHE.pretty").mkdir(exist_ok=True)
    files = {
        "continuity.kicad_pro": '{"meta": {"filename": "continuity.kicad_pro", "version": 1}}',
        "EHE.kicad_sym": f'(kicad_symbol_lib (version 20241209) (generator "ehe") (symbol "Terminal" {SYMBOL}))',
        "EHE.pretty/Terminal.kicad_mod": footprint("Terminal"),
        "sym-lib-table": '(sym_lib_table (version 7) (lib (name "EHE") (type "KiCad") (uri "@{KIPRJMOD}/EHE.kicad_sym") (options "") (descr "")))',
        "fp-lib-table": '(fp_lib_table (version 7) (lib (name "EHE") (type "KiCad") (uri "@{KIPRJMOD}/EHE.pretty") (options "") (descr "")))',
        "continuity.kicad_sch": f'''(kicad_sch (version 20250114) (generator "ehe") (uuid "{uid(NAME)}") (paper "A4")
          (lib_symbols (symbol "EHE:Terminal" {SYMBOL}))
          (wire (pts (xy 53.34 50.8) (xy 78.74 50.8)) (stroke (width 0) (type default)) (uuid "{uid('wire')}"))
          (label "LINK" (at 53.34 50.8 0) (effects (font (size 1.27 1.27)) (justify left bottom)) (uuid "{uid('label')}"))
          {symbol('J1', 50.8, 180)}
          {symbol('J2', 81.28, 0)}
          (sheet_instances (path "/" (page "1"))))''',
        "continuity.kicad_pcb": f'''(kicad_pcb (version 20241229) (generator "ehe")
          (general (thickness 1.6))
          (paper "A4")
          (layers (0 "F.Cu" signal) (31 "B.Cu" signal)
            (36 "B.SilkS" user "B.Silkscreen") (37 "F.SilkS" user "F.Silkscreen")
            (38 "B.Mask" user) (39 "F.Mask" user) (44 "Edge.Cuts" user)
            (46 "B.CrtYd" user "B.Courtyard") (47 "F.CrtYd" user "F.Courtyard")
            (48 "B.Fab" user) (49 "F.Fab" user))
          (setup (pad_to_mask_clearance 0))
          (net 0 "") (net 1 "LINK")
          {footprint('EHE:Terminal', 'J1', 30)}
          {footprint('EHE:Terminal', 'J2', 40)}
          (gr_rect (start 26 25) (end 44 35) (stroke (width 0.05) (type default)) (fill none) (layer "Edge.Cuts") (uuid "{uid('edge')}"))
          (segment (start 30 30) (end 40 30) (width 0.25) (layer "F.Cu") (net 1) (uuid "{uid('track')}")))''',
    }
    for name, data in files.items():
        with (root / name).open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(data.replace("@{KIPRJMOD}", "$" + "{KIPRJMOD}") + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    build(parser.parse_args().output)
