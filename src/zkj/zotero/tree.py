"""The collection tree — the researcher's own outline.

Subcollections are chapters. Their paths are compared against what the card
texts cluster into later, so the path of a node is part of the data model and
not a display detail.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any

from .errors import ZoteroError
from .models import Collection

PATH_SEP = "/"


@dataclass
class CollectionNode:
    key: str
    name: str
    parent_key: str | None
    children: list[CollectionNode] = field(default_factory=list)
    path: str = ""
    depth: int = 0

    def walk(self) -> Iterator[CollectionNode]:
        yield self
        for child in self.children:
            yield from child.walk()


class CollectionTree:
    """Every collection in the library, linked up and given a path."""

    def __init__(self, nodes: dict[str, CollectionNode], roots: list[CollectionNode]):
        self.nodes = nodes
        self.roots = roots

    @classmethod
    def from_payloads(cls, payloads: Iterable[dict[str, Any]]) -> CollectionTree:
        nodes: dict[str, CollectionNode] = {}
        for payload in payloads:
            c = Collection.from_payload(payload)
            nodes[c.key] = CollectionNode(
                key=c.key, name=c.name, parent_key=c.parent_key
            )

        for node in nodes.values():
            # A collection whose parent is missing from the payload — a shared
            # library, or a partial response — is treated as a root rather than
            # dropped, so no cards go missing with it.
            if node.parent_key and node.parent_key in nodes:
                nodes[node.parent_key].children.append(node)

        roots = [
            n for n in nodes.values() if not n.parent_key or n.parent_key not in nodes
        ]
        roots.sort(key=lambda n: n.name.lower())

        seen: set[str] = set()

        def assign(node: CollectionNode, prefix: str, depth: int) -> None:
            if node.key in seen:  # a cycle in the data must not hang the app
                return
            seen.add(node.key)
            node.path = f"{prefix}{PATH_SEP}{node.name}" if prefix else node.name
            node.depth = depth
            node.children.sort(key=lambda n: n.name.lower())
            for child in node.children:
                assign(child, node.path, depth + 1)

        for root in roots:
            assign(root, "", 0)
        return cls(nodes, roots)

    # -- lookups -----------------------------------------------------------

    def __contains__(self, key: object) -> bool:
        return key in self.nodes

    def __len__(self) -> int:
        return len(self.nodes)

    def get(self, key: str) -> CollectionNode:
        try:
            return self.nodes[key]
        except KeyError:
            raise ZoteroError(f"Collection {key} is not in this library.") from None

    def by_path(self, path: str) -> CollectionNode | None:
        for node in self.nodes.values():
            if node.path == path:
                return node
        return None

    def child_named(self, parent_key: str | None, name: str) -> CollectionNode | None:
        for node in self.nodes.values():
            if node.parent_key == parent_key and node.name == name:
                return node
        return None

    def subtree(self, root_key: str) -> list[CollectionNode]:
        """The root and everything under it, parents before children."""
        return list(self.get(root_key).walk())

    def subtree_keys(self, root_key: str) -> list[str]:
        return [n.key for n in self.subtree(root_key)]
