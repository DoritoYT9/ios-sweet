import re

path = "tools/mkpayload.py"
with open(path, "r") as f:
    content = f.read()

new_func = """def adt_dram(adt_blob):
    \"\"\"Kernel-managed RAM <base, size> from the ADT\'s mcc or iop-pmp-nub.\"\"\"
    root, _ = adt.parse(adt_blob)
    def find_node(node, name):
        if node.name == name:
            return node
        for c in node.children:
            res = find_node(c, name)
            if res: return res
        return None

    pmp = find_node(root, "iop-pmp-nub")
    if pmp and pmp.prop("region-base"):
        base = struct.unpack("<Q", pmp.prop("region-base"))[0]
        size = struct.unpack("<Q", pmp.prop("region-size"))[0]
        return (base, size)

    return (0x100000000, 0x40000000)"""

new_content, count = re.subn(r"def adt_dram\(adt_blob\):.*?(?=\ndef build)", new_func + "\n\n", content, flags=re.DOTALL)
if count > 0:
    with open(path, "w") as f:
        f.write(new_content)
    print("¡Parche actualizado a Highmem con éxito!")
else:
    print("Error: no se ha encontrado la función.")
