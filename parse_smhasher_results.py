#!/usr/bin/env python3
"""
Parse SMHasher3 result files and extract failures to CSV.

File naming convention: rounds=<N>-last=<t|f>-<hashname>.txt
Failures are lines ending with multiple exclamation marks (!!).
"""

import csv
import re
import sys
from pathlib import Path


def parse_filename(filename: str) -> dict | None:
    """
    Parse filename to extract rounds, last, and hash function name.
    Expected format: rounds=<N>-last=<t|f>-<hashname>.txt
    """
    pattern = r'^rounds=(\d+)-last=([tf])-(.+)\.txt$'
    match = re.match(pattern, filename)
    if not match:
        return None
    return {
        'rounds': match.group(1),
        'last': match.group(2),
        'hash': match.group(3),
    }


def normalize_bitspec(bitspec: str) -> str:
    """
    Normalize bitspec:
    - '64-bit', '32-bit', '128-bit' (without high/low/any qualifier) -> 'all'
    - Keep qualified specs as-is (high 32-bit, low 32-bit, any 8..15 bits, etc.)
    """
    bitspec = bitspec.strip()
    # Match unqualified full-width bitspecs (no high/low/any prefix)
    if re.match(r'^\d+-bit$', bitspec):
        return 'all'
    return bitspec


def extract_bitspec_from_collision_line(line: str) -> str | None:
    """
    Extract bitspec from collision/distribution test lines.
    Examples:
    - 'Testing all collisions (      64-bit)' -> '64-bit' -> 'all'
    - 'Testing all collisions (high  32-bit)' -> 'high 32-bit'
    - 'Testing all collisions (low   32-bit)' -> 'low 32-bit'
    - 'Testing all collisions (high 16..35 bits)' -> 'high 16..35 bits'
    - 'Testing distribution   (any   8..15 bits)' -> 'any 8..15 bits'
    """
    # Match collision/distribution lines specifically
    # The bitspec is in the first parenthesis after "collisions" or "distribution"
    match = re.search(r'Testing (?:all collisions|distribution)\s+\(([^)]+)\)', line)
    if match:
        raw = match.group(1)
        # Normalize whitespace
        raw = ' '.join(raw.split())
        return normalize_bitspec(raw)
    return None


def extract_bitspec_from_avalanche_line(line: str) -> str | None:
    """
    Extract bitspec from avalanche test lines.
    Example: 'max is 100.0% at bit   32 -> out   0' -> 'bit 32 -> out 0'
    """
    match = re.search(r'at bit\s+(\d+)\s*->\s*out\s+(\d+)', line)
    if match:
        return f"bit {match.group(1)} -> out {match.group(2)}"
    return None


def extract_bitspec_from_bic_line(line: str) -> str | None:
    """
    Extract bitspec from BIC (Bit Independence Criteria) test lines.
    Example: 'max 1.0000 at bit   32 -> out (  0,  1)' -> 'bit 32 -> out (0, 1)'
    """
    match = re.search(r'at bit\s+(\d+)\s*->\s*out\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)', line)
    if match:
        return f"bit {match.group(1)} -> out ({match.group(2)}, {match.group(3)})"
    return None


def extract_bitspec(line: str, test_family: str) -> str:
    """
    Extract and normalize bitspec from a failure line based on test family context.
    """
    # Try collision/distribution format first (most common)
    bitspec = extract_bitspec_from_collision_line(line)
    if bitspec:
        return bitspec

    # Try avalanche format
    bitspec = extract_bitspec_from_avalanche_line(line)
    if bitspec:
        return bitspec

    # Try BIC format
    bitspec = extract_bitspec_from_bic_line(line)
    if bitspec:
        return bitspec

    # Unknown format - return empty string
    return ''


def parse_test_family(line: str) -> str | None:
    """
    Parse test family from section marker.
    Example: '[[[ Avalanche Tests ]]]' -> 'Avalanche Tests'
    """
    match = re.match(r'^\[\[\[\s*(.+?)\s*\]\]\]$', line.strip())
    if match:
        return match.group(1)
    return None


def parse_test_instance(line: str, test_family: str) -> str | None:
    """
    Parse test instance from a line, based on test family context.
    Returns the instance identifier or None if not an instance line.
    """
    line = line.strip()

    # Keyset/Seed instance lines: 'Keyset 'Name' - description - N keys'
    # or 'Seed 'Name' - description - N keys'
    if re.match(r"^(Keyset|Seed)\s+'", line):
        # Return the whole description (minus trailing stats sometimes)
        return line

    # Avalanche/BIC instance lines: 'Testing  N-byte keys, ...'
    # These appear at the start of test output within Avalanche/BIC families
    if 'Avalanche' in test_family or 'BIC' in test_family or 'Bitflip' in test_family:
        match = re.match(r'^Testing\s+(\d+-byte keys)', line)
        if match:
            return match.group(1)

    # Seed BlockLength/BlockOffset tests have different instance formats
    if 'BlockLength' in test_family or 'BlockOffset' in test_family:
        # Lines like 'Testing  N-byte seeds, ...'
        match = re.match(r'^Testing\s+(\d+-byte seeds)', line)
        if match:
            return match.group(1)

    return None


def is_failure_line(line: str) -> bool:
    """
    Check if a line indicates a test failure.
    Failures are marked with multiple exclamation marks at the end.
    """
    return bool(re.search(r'!{2,}\s*$', line))


def parse_file(filepath: Path) -> list[dict]:
    """
    Parse a single SMHasher result file and extract all failures.
    """
    file_info = parse_filename(filepath.name)
    if not file_info:
        print(f"Warning: Could not parse filename: {filepath.name}", file=sys.stderr)
        return []

    failures = []
    current_family = ''
    current_instance = ''

    with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            line_stripped = line.strip()

            # Check for test family marker
            family = parse_test_family(line_stripped)
            if family:
                current_family = family
                current_instance = ''  # Reset instance when entering new family
                continue

            # Check for test instance
            instance = parse_test_instance(line_stripped, current_family)
            if instance:
                current_instance = instance
                # Instance lines can also be failure lines (e.g., Avalanche tests)
                # So don't continue here, check for failure below

            # Check for failure
            if is_failure_line(line_stripped):
                bitspec = extract_bitspec(line_stripped, current_family)
                failures.append({
                    'rounds': file_info['rounds'],
                    'last': file_info['last'],
                    'hash': file_info['hash'],
                    'test_family': current_family,
                    'test_instance': current_instance,
                    'bitspec': bitspec,
                })

    return failures


def main():
    """
    Main entry point. Parse all result files in the specified directory
    and output failures as CSV to stdout.
    """
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <results_directory>", file=sys.stderr)
        sys.exit(1)

    results_dir = Path(sys.argv[1])
    if not results_dir.is_dir():
        print(f"Error: {results_dir} is not a directory", file=sys.stderr)
        sys.exit(1)

    # Find all .txt files matching the expected naming pattern
    result_files = sorted(results_dir.glob('rounds=*-last=*-*.txt'))

    if not result_files:
        print(f"Warning: No matching result files found in {results_dir}", file=sys.stderr)
        sys.exit(0)

    all_failures = []
    for filepath in result_files:
        failures = parse_file(filepath)
        all_failures.extend(failures)

    # Output CSV
    fieldnames = ['rounds', 'last', 'hash', 'test_family', 'test_instance', 'bitspec']
    writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
    writer.writeheader()
    for failure in all_failures:
        writer.writerow(failure)


if __name__ == '__main__':
    main()
