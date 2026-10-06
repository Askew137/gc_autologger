"""
Module for converting Geocaching GC codes to numeric cacheId and vice versa.
Adapted from c:geo (GCUtils.java).
Combines Base16 for legacy caches and Base31 for modern caches.
"""

from typing import Optional

SEQUENCE_GCID = "0123456789ABCDEFGHJKMNPQRTVWXYZ"
MAP_GCID = {c: i for i, c in enumerate(SEQUENCE_GCID)}
GC_BASE31 = 31
GC_BASE16 = 16


def gc_code_to_cache_id(code: Optional[str]) -> int:
    """
    Convert a Geocaching code (e.g. 'GC2MEGA') to a numeric cacheId (e.g. 2045702).
    
    This numeric ID is required by the internal Geocaching.com endpoint:
    POST https://www.geocaching.com/seek/geocache.usercoordinate
    
    :param code: String representation of the GC code (e.g. 'GC12345', 'GC2MEGA')
    :return: Integer representing the internal cacheId, or 0 on error.
    """
    if not code:
        return 0
    
    code = code.strip().upper()
    if not code.startswith("GC"):
        return 0
    
    # Strip the 'GC' prefix
    clean = code[2:]
    if not clean:
        return 0
    
    # Base selection:
    # Legacy caches with IDs up to 65535 (FFFF) used Base16 hexadecimal.
    # Modern caches use Base31 with a 31-char alphabet (omitting I, L, O, S, U).
    if len(clean) < 4 or (len(clean) == 4 and MAP_GCID.get(clean[0], 0) < 16):
        base = GC_BASE16
    else:
        base = GC_BASE31
    
    gcid = 0
    for ch in clean:
        idx = MAP_GCID.get(ch, -1)
        if idx < 0:
            return 0  # Invalid character in code
        gcid = base * gcid + idx
        
    # If Base31 was used, add the offset: 16^4 - 16 * 31^3 = -411120
    if base == GC_BASE31:
        gcid += (16 ** 4) - 16 * (31 ** 3)
        
    return gcid


def cache_id_to_gc_code(cache_id: int) -> str:
    """
    Reverse conversion from numeric cacheId to official GC code (e.g. 2045702 -> 'GC2MEGA').
    """
    if cache_id <= 0:
        return ""
    
    is_low_number = cache_id <= 65535
    base = GC_BASE16 if is_low_number else GC_BASE31
    div_result = cache_id if is_low_number else cache_id + 411120
    
    chars = []
    while div_result > 0:
        rest = div_result % base
        div_result = div_result // base
        chars.append(SEQUENCE_GCID[rest])
        
    chars.reverse()
    return "GC" + "".join(chars)


if __name__ == "__main__":
    # Test suite verified against c:geo unit tests (GCUtilsTest.java)
    test_cases = {
        "GC2MEGA": 2045702,
        "GC1PKK9": 1186660,
        "GC1234": 4660,
        "GCF123": 61731,
        "GC30": 48,
        "GC28": 40,
        "GC1": 1,
        "GCE": 14,
        "GCFFFF": 65535,
        "GCG000": 65536,
    }
    for code, expected in test_cases.items():
        res = gc_code_to_cache_id(code)
        back = cache_id_to_gc_code(res)
        assert res == expected, f"Error: {code} -> {res} != {expected}"
        assert back == code, f"Reverse error: {res} -> {back} != {code}"
    print("All GC code conversions match c:geo specifications!")
