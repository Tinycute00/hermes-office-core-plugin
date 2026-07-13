from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
import posixpath
from pathlib import PurePosixPath
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET
import zipfile


SCHEMAS = "http://schemas.openxmlformats.org"
PURL = "http://purl.oclc.org/ooxml"
CONTENT_TYPES_NAMESPACE = f"{SCHEMAS}/package/2006/content-types"
PACKAGE_RELATIONSHIPS_NAMESPACE = f"{SCHEMAS}/package/2006/relationships"
MAIN_NAMESPACES = {
    "spreadsheet": (f"{SCHEMAS}/spreadsheetml/2006/main", f"{PURL}/spreadsheetml/main"),
    "word": (f"{SCHEMAS}/wordprocessingml/2006/main", f"{PURL}/wordprocessingml/main"),
    "presentation": (f"{SCHEMAS}/presentationml/2006/main", f"{PURL}/presentationml/main"),
}


def qualified_names(namespaces: tuple[str, str], local: str) -> frozenset[str]:
    return frozenset(f"{{{namespace}}}{local}" for namespace in namespaces)


def relationship_types(name: str) -> frozenset[str]:
    return frozenset(
        (
            f"{SCHEMAS}/officeDocument/2006/relationships/{name}",
            f"{PURL}/officeDocument/relationships/{name}",
        )
    )


@dataclass(frozen=True, slots=True)
class DependentPart:
    relationship_member: str
    relationship_types: frozenset[str]
    roots: frozenset[str]
    content_types: frozenset[str]
    description: str


@dataclass(frozen=True, slots=True)
class PackageProfile:
    main_member: str
    main_roots: frozenset[str]
    main_content_types: frozenset[str]
    dependent: DependentPart | None


RELATIONSHIPS_ROOTS = frozenset({f"{{{PACKAGE_RELATIONSHIPS_NAMESPACE}}}Relationships"})
RELATIONSHIP_TAG = f"{{{PACKAGE_RELATIONSHIPS_NAMESPACE}}}Relationship"
RELATIONSHIP_CONTENT_TYPES = frozenset({"application/vnd.openxmlformats-package.relationships+xml"})
OFFICE_DOCUMENT_RELATIONSHIP_TYPES = relationship_types("officeDocument")
PROFILES = {
    ".xlsx": PackageProfile(
        "xl/workbook.xml",
        qualified_names(MAIN_NAMESPACES["spreadsheet"], "workbook"),
        frozenset(
            {
                "application/vnd.openxmlformats-officedocument.spreadsheetml.main+xml",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
            }
        ),
        DependentPart(
            "xl/_rels/workbook.xml.rels",
            relationship_types("worksheet"),
            qualified_names(MAIN_NAMESPACES["spreadsheet"], "worksheet"),
            frozenset({"application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"}),
            "worksheet",
        ),
    ),
    ".docx": PackageProfile(
        "word/document.xml",
        qualified_names(MAIN_NAMESPACES["word"], "document"),
        frozenset({"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"}),
        None,
    ),
    ".pptx": PackageProfile(
        "ppt/presentation.xml",
        qualified_names(MAIN_NAMESPACES["presentation"], "presentation"),
        frozenset({"application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"}),
        DependentPart(
            "ppt/_rels/presentation.xml.rels",
            relationship_types("slide"),
            qualified_names(MAIN_NAMESPACES["presentation"], "sld"),
            frozenset({"application/vnd.openxmlformats-officedocument.presentationml.slide+xml"}),
            "slide",
        ),
    ),
}


class OpenXMLValidationError(ValueError):
    pass


def declared_content_type(root: ET.Element, member: str) -> str | None:
    override_tag = f"{{{CONTENT_TYPES_NAMESPACE}}}Override"
    default_tag = f"{{{CONTENT_TYPES_NAMESPACE}}}Default"
    default_content_type = None
    extension = (
        "rels"
        if member.endswith(".rels")
        else PurePosixPath(member).suffix.removeprefix(".").lower()
    )
    for declaration in root:
        if (
            declaration.tag == override_tag
            and declaration.attrib.get("PartName") == f"/{member}"
        ):
            return declaration.attrib.get("ContentType")
        if (
            declaration.tag == default_tag
            and declaration.attrib.get("Extension", "").lower() == extension
        ):
            default_content_type = declaration.attrib.get("ContentType")
    return default_content_type


def require_content_type(root: ET.Element, member: str, expected: frozenset[str]) -> None:
    content_type = declared_content_type(root, member)
    if content_type is None or content_type.lower() not in expected:
        raise OpenXMLValidationError(
            f"Candidate package has an invalid content type for {member}."
        )


def relationship_base(member: str) -> PurePosixPath:
    if member == "_rels/.rels":
        return PurePosixPath()
    marker = "/_rels/"
    if marker not in member or not member.endswith(".rels"):
        raise OpenXMLValidationError(f"Invalid relationship part path: {member}")
    prefix, relation_name = member.rsplit(marker, 1)
    source_name = relation_name.removesuffix(".rels")
    return PurePosixPath(prefix, source_name).parent


def relationship_target(member: str, target: str) -> str:
    parsed = urlsplit(target)
    path = unquote(parsed.path).replace("\\", "/")
    if not path:
        raise OpenXMLValidationError(f"Relationship in {member} has an empty target.")
    if path.startswith("/"):
        normalized = posixpath.normpath(path.lstrip("/"))
    else:
        normalized = posixpath.normpath(
            posixpath.join(relationship_base(member).as_posix(), path)
        )
    if normalized in {"", ".", ".."} or normalized.startswith("../"):
        raise OpenXMLValidationError(
            f"Relationship in {member} escapes the package: {target}"
        )
    return normalized


def parse_xml_parts(package: zipfile.ZipFile, names: set[str]) -> dict[str, ET.Element]:
    roots: dict[str, ET.Element] = {}
    for name in sorted(names):
        if not (name.endswith(".xml") or name.endswith(".rels")):
            continue
        try:
            with package.open(name) as handle:
                roots[name] = ET.parse(handle).getroot()
        except (ET.ParseError, OSError, KeyError) as error:
            raise OpenXMLValidationError(f"Candidate package has invalid XML: {name}") from error
    return roots


def validate_relationships(roots: dict[str, ET.Element], names: set[str]) -> None:
    for member, root in roots.items():
        if not member.endswith(".rels"):
            continue
        if root.tag not in RELATIONSHIPS_ROOTS:
            raise OpenXMLValidationError(
                f"Candidate relationship part has the wrong root: {member}"
            )
        for relation in root:
            if relation.tag != RELATIONSHIP_TAG:
                continue
            if relation.attrib.get("TargetMode", "").lower() == "external":
                continue
            target = relation.attrib.get("Target")
            if not target:
                raise OpenXMLValidationError(
                    f"Candidate relationship has no target: {member}"
                )
            resolved = relationship_target(member, target)
            if resolved not in names:
                raise OpenXMLValidationError(
                    f"Candidate relationship target is missing: {resolved}"
                )


def internal_targets(root: ET.Element, member: str, types: frozenset[str]) -> Iterator[str]:
    for relation in root:
        if relation.tag != RELATIONSHIP_TAG:
            continue
        if relation.attrib.get("TargetMode", "").lower() == "external":
            continue
        if relation.attrib.get("Type") not in types:
            continue
        target = relation.attrib.get("Target")
        if target:
            yield relationship_target(member, target)


def require_package_relationship(root: ET.Element, profile: PackageProfile) -> None:
    if profile.main_member in internal_targets(
        root, "_rels/.rels", OFFICE_DOCUMENT_RELATIONSHIP_TYPES
    ):
        return
    raise OpenXMLValidationError(
        "Candidate package has no required officeDocument relationship."
    )


def require_dependent_part(
    roots: dict[str, ET.Element],
    content_types: ET.Element,
    dependent: DependentPart,
) -> None:
    require_content_type(content_types, dependent.relationship_member, RELATIONSHIP_CONTENT_TYPES)
    found = False
    for resolved in internal_targets(
        roots[dependent.relationship_member],
        dependent.relationship_member,
        dependent.relationship_types,
    ):
        found = True
        part = roots.get(resolved)
        if part is None or part.tag not in dependent.roots:
            raise OpenXMLValidationError(
                f"Candidate package has an invalid {dependent.description} root: {resolved}"
            )
        require_content_type(content_types, resolved, dependent.content_types)
    if not found:
        raise OpenXMLValidationError(
            f"Candidate package has no required {dependent.description} relationship."
        )


def validate_openxml(package: zipfile.ZipFile, extension: str) -> None:
    names = package.namelist()
    if len(names) != len(set(names)):
        raise OpenXMLValidationError("Candidate package contains duplicate part names.")
    profile = PROFILES.get(extension)
    if profile is None:
        raise OpenXMLValidationError(f"Unsupported Open XML extension: {extension}")
    name_set = set(names)
    roots = parse_xml_parts(package, name_set)
    content_types = roots.get("[Content_Types].xml")
    if content_types is None or content_types.tag != f"{{{CONTENT_TYPES_NAMESPACE}}}Types":
        raise OpenXMLValidationError(
            "Candidate package has an invalid XML root: [Content_Types].xml"
        )
    main_root = roots.get(profile.main_member)
    if main_root is None or main_root.tag not in profile.main_roots:
        raise OpenXMLValidationError(
            f"Candidate package has an invalid XML root: {profile.main_member}"
        )
    require_content_type(content_types, profile.main_member, profile.main_content_types)
    package_relationships = roots.get("_rels/.rels")
    if package_relationships is None:
        raise OpenXMLValidationError(
            "Candidate package has no required officeDocument relationship."
        )
    if package_relationships.tag not in RELATIONSHIPS_ROOTS:
        raise OpenXMLValidationError(
            "Candidate package has an invalid XML root: _rels/.rels"
        )
    require_content_type(content_types, "_rels/.rels", RELATIONSHIP_CONTENT_TYPES)
    if profile.dependent is not None:
        relation_root = roots.get(profile.dependent.relationship_member)
        if relation_root is None or relation_root.tag not in RELATIONSHIPS_ROOTS:
            raise OpenXMLValidationError(
                f"Candidate package has an invalid XML root: {profile.dependent.relationship_member}"
            )
    validate_relationships(roots, name_set)
    require_package_relationship(package_relationships, profile)
    if profile.dependent is not None:
        require_dependent_part(roots, content_types, profile.dependent)
