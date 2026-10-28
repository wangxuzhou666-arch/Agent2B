"""Fixture plugin dropped in via TWOTRACK_PLUGIN_PATHS: a category entity extending widget."""

from twotrack.core import EntitySpec, FieldSpec, FieldType, Plugin

PLUGIN = Plugin(
    name="gizmos",
    version="0.1.0",
    entities=(
        EntitySpec(
            id="gizmo",
            label="Gizmo",
            extends="widget",
            attrs=(FieldSpec(id="voltage", label="Voltage", type=FieldType.FLOAT, unit="V",
                             aliases=("volts",)),),
        ),
    ),
)
