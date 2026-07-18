#!/usr/bin/env python3
"""Validate CIDR syntax and reject overlapping network ranges."""

from __future__ import annotations

import argparse
import ipaddress
from typing import cast


def _network(value: str) -> ipaddress.IPv4Network:
    network = ipaddress.ip_network(value, strict=False)
    if not isinstance(network, ipaddress.IPv4Network):
        raise argparse.ArgumentTypeError(f"IPv4 CIDR required: {value}")
    return network


def main() -> int:
    """Validate all supplied networks and report the first overlap."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("cidr", nargs="*", type=_network)
    parser.add_argument("--address-in", nargs=2, metavar=("ADDRESS", "CIDR"))
    parser.add_argument("--first-host", type=_network)
    args = parser.parse_args()

    if not args.cidr and not args.address_in and not args.first_host:
        parser.error("provide CIDRs, --address-in, or --first-host")

    for index, left in enumerate(args.cidr):
        for right in args.cidr[index + 1 :]:
            if left.overlaps(right):
                parser.error(f"CIDR overlap: {left} and {right}")

    if args.address_in:
        address_value, network_value = cast(tuple[str, str], args.address_in)
        try:
            address = ipaddress.ip_address(address_value)
            network = _network(network_value)
        except ValueError as error:
            parser.error(str(error))
        if address not in network:
            parser.error(f"address {address} is outside {network}")

    if args.first_host:
        try:
            print(next(args.first_host.hosts()))
        except StopIteration:
            parser.error(f"network has no host address: {args.first_host}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
