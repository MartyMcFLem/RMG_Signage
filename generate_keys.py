#!/usr/bin/env python3
"""Generateur de cles de licence RMG Signage (usage admin).

Usage:
    python3 generate_keys.py                    # 1 cle standard
    python3 generate_keys.py --tier enterprise  # 1 cle enterprise
    python3 generate_keys.py --tier professional --count 10  # 10 cles pro
    python3 generate_keys.py --list-tiers       # afficher les tiers
"""
import argparse
import sys
import os

# Importer le systeme de licence depuis upload.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from upload import generate_license_key, validate_license_key, LICENSE_TIERS


def main():
    parser = argparse.ArgumentParser(description="Generateur de cles de licence RMG Signage")
    parser.add_argument("--tier", default="standard",
                        help="Tier de licence (starter/standard/professional/enterprise/unlimited)")
    parser.add_argument("--count", type=int, default=1, help="Nombre de cles a generer")
    parser.add_argument("--list-tiers", action="store_true", help="Afficher les tiers disponibles")
    parser.add_argument("--validate", type=str, help="Valider une cle existante")
    args = parser.parse_args()

    if args.list_tiers:
        print("\nTiers disponibles :")
        print(f"  {'Tier':<15} {'Code':<6} {'Quota'}")
        print(f"  {'-'*15} {'-'*6} {'-'*10}")
        for code, info in sorted(LICENSE_TIERS.items()):
            quota_gb = info['quota_mb'] / 1024
            print(f"  {info['name']:<15} 0x{code:02x}   {quota_gb:.0f} Go")
        return

    if args.validate:
        valid, tier, quota = validate_license_key(args.validate)
        if valid:
            print(f"Cle VALIDE : tier={tier}, quota={quota} MB ({quota//1024} Go)")
        else:
            print("Cle INVALIDE")
        return

    # Trouver le code du tier
    tier_code = None
    for code, info in LICENSE_TIERS.items():
        if info["name"] == args.tier:
            tier_code = code
            break

    if tier_code is None:
        print(f"Tier inconnu: {args.tier}")
        print("Tiers valides: " + ", ".join(info["name"] for info in LICENSE_TIERS.values()))
        sys.exit(1)

    tier_info = LICENSE_TIERS[tier_code]
    print(f"\nGeneration de {args.count} cle(s) [{args.tier} / {tier_info['quota_mb']//1024} Go] :\n")

    for _ in range(args.count):
        key = generate_license_key(tier_code)
        # Verification
        valid, _, _ = validate_license_key(key)
        status = "OK" if valid else "ERREUR"
        print(f"  {key}  [{status}]")

    print()


if __name__ == "__main__":
    main()
