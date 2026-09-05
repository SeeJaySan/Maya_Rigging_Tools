"""Build Biped: guides phase + skeleton phase in one tool.

Replaces Skeleton From Guides. Composite of: Build Locators, then the
guides-to-skeleton build (joints named {guide}_JNT, oriented,
assembled: thighs under pelvis, clavicles under chest).

Pick the phase with the action parameter: guides builds the guides,
you place them, then skeleton builds the joints from them.
"""
import logging

import maya.cmds as mc

from modules.rig.Lib import scene_meta
from modules.rig.Lib import build_locators

logger = logging.getLogger(__name__)

TOOL_META = {
    "order": 1,
    "description": (
        "Build a biped in two phases, one button.\n\n"
        "guides: builds the guides (place them to fit the "
        "character). skeleton: builds the joints from the placed guides, "
        "named {guide}_JNT (the "
        "name the IKFK builders expect), oriented X down the bone with Z "
        "as the main rotation axis, and "
        "end joints zeroed, thighs parented "
        "under the pelvis and clavicles under the chest, all under "
        "BN_Skeleton.\n\n"
        "You only place the L_ guides: the R_ chains live under a mirror "
        "group and are driven from their L_ twin, so they follow as you "
        "move or rotate the left side.\n\n"
        "twist joints: extra BN joints spaced along each limb segment "
        "(thigh, shin, upper arm, forearm), created UNDRIVEN as part of "
        "the export skeleton. Body Setup's twist option then drives them "
        "(roll or ribbon), so the driver can be swapped later without "
        "re-skinning.\n\n"
        "Run BEFORE: Body Setup.\n"
        "Pick the phase with the action buttons."
    ),
    "params": {
        "action": {
            "label": "action",
            "choices": ["guides", "skeleton"],
            "radio": True,
            "tooltip": "guides: build/refresh the guides. "
                       "skeleton: build the joints from the placed guides.",
        },
        "primary_axis": {
            "label": "primary (aim)",
            "choices": ["x", "y", "z"],
            "radio": True,
            "tooltip": "Joint axis that points down the bone.",
        },
        "secondary_axis": {
            "label": "secondary (rotate)",
            "choices": ["x", "y", "z"],
            "radio": True,
            "tooltip": "Joint axis that becomes the main rotation axis "
                       "(knee, elbow, toe roll): perpendicular to the bend "
                       "plane. The third axis aims at the character's front.",
        },
        "guides_group": {
            "label": "guides group",
            "tooltip": "Top group holding the guide chains.",
        },
        "axis_size": {
            "label": "guide axis length",
            "min": 0.1,
            "max": 100.0,
            "tooltip": "Length of the guides' RGB axis arrows (red X = bone "
                       "direction). Stored as TOOLSET_META.guideAxisLength "
                       "and drives every tripod live.",
        },
        "axis_width": {
            "label": "guide axis width",
            "min": 0.1,
            "max": 100.0,
            "tooltip": "Thickness of the arrows, independent of length. "
                       "Stored as TOOLSET_META.guideAxisWidth, live.",
        },
        "twist_joints": {
            "label": "twists / segment",
            "min": 0,
            "max": 5,
            "tooltip": "Undriven twist BN joints per limb segment "
                       "(0 = none). Body Setup drives them later. 3 gives "
                       "a center joint, the minimum for ribbon bend to "
                       "read smoothly; 2 is enough for roll-only.",
        },
    },
}

# limb segments that receive twist joints:
# (module, top joint, child joint, twist name label)
TWIST_SEGMENTS = [
    ("Arm", "Shoulder", "Elbow", "UpperArm"),
    ("Arm", "Elbow", "Wrist", "Forearm"),
    ("Leg", "Thigh", "Calf", "Thigh"),
    ("Leg", "Calf", "Ankle", "Shin"),
]


def main(action="guides", primary_axis="x", secondary_axis="z",
         guides_group="Guides", axis_size=3.0, axis_width=3.0,
         twist_joints=3, *args):
    action = (action or "guides").lower()
    primary_axis = (primary_axis or "x").lower()
    secondary_axis = (secondary_axis or "z").lower()
    if primary_axis == secondary_axis:
        mc.warning("primary and secondary axis must differ; using x / z.")
        primary_axis, secondary_axis = "x", "z"
    guides_group = guides_group or "Guides"
    twist_joints = int(twist_joints)
    axis_size = float(axis_size)
    axis_width = float(axis_width)

    if action == "skeleton":
        return build_skeleton(guides_group, twist_joints,
                              primary_axis, secondary_axis)
    return build_guides_phase(guides_group, axis_size, axis_width)


def build_guides_phase(guides_group="Guides", axis_size=3.0,
                       axis_width=3.0):
    existing = scene_meta.find("guides", name_fallback=guides_group)
    if existing:
        added = build_locators.ensure_foot_pivot_guides("L", existing)
        made, linked = build_locators.mirror_guides(existing)
        added += made
        logger.info("mirror guides: %d built, %d driven", len(made), linked)
        # limbs are NOT auto-straightened: the user places the bend, and the
        # skeleton reads it as the fold direction. straighten_limb_guides()
        # in Lib/build_locators is there for manual use if wanted.
        tripods, oriented = build_locators.upgrade_guide_display(
            existing, size=axis_size, width=axis_width)
        logger.info("guide display: %d tripods added, %d guides oriented, "
                    "axis size %s", tripods, oriented, axis_size)
        msg = ("Guides already exist ({}). Place them, then Run with "
               "action: skeleton.".format(existing))
        if added:
            msg += " Added missing foot pivot guides: {}.".format(
                ", ".join(added))
        mc.warning(msg)
        mc.select(existing)
        return existing
    made = build_locators.build_guides("L")
    build_locators.set_axis_size(axis_size, axis_width, top=guides_group)
    mc.warning("Guides built. PLACE THEM to fit the character, then Run "
               "with action: skeleton.")
    return made


def _chain_roots(guides_group):
    """Guide chain roots, reaching through container groups.

    The driven R_ chains sit one level down under the mirror group, which
    is an untagged transform: descend into anything that is not a guide
    so the container never becomes a joint itself.
    """
    roots = []
    for child in mc.listRelatives(guides_group, children=True,
                                  type="transform", fullPath=True) or []:
        if build_locators.is_guide(child):
            roots.append(child)
            continue
        roots += [c for c in (mc.listRelatives(child, children=True,
                                               type="transform",
                                               fullPath=True) or [])
                  if build_locators.is_guide(c)]
    return roots


def _build_joints(guide, parent_joint):
    """Recursively create a joint per guide, named {guide}_JNT.

    Guides ending in _GD are pivot-placement guides (heel, bank edges),
    not bones: no joints for them.
    """
    short = guide.split("|")[-1]
    if short.endswith("_GD"):
        return None
    name = short + "_JNT"
    if mc.objExists(name):
        mc.warning("{} already exists; skipping this chain.".format(name))
        return None
    mc.select(clear=True)
    jnt = mc.joint(name=name, position=mc.xform(guide, q=True, ws=True, t=True))
    if parent_joint:
        mc.parent(jnt, parent_joint)
    for child in mc.listRelatives(guide, children=True, type="transform", fullPath=True) or []:
        if build_locators.is_guide(child):
            _build_joints(child, jnt)
    return jnt


def _orient_chain(root, primary="x", secondary="z"):
    # convention (default): X down the bone, Z the main rotation axis
    # (knee, elbow, toe roll). Maya's orientJoint string is aim + up +
    # third, and the "up" axis is aimed at a world direction, so the
    # user's rotation axis goes in the THIRD slot and the leftover axis is
    # the one that gets aimed. "zup" aims it at world +Z, which leaves the
    # rotation axis perpendicular to the bend plane for both vertical
    # chains (legs) and horizontal ones (arms). The only singular case is
    # a bone running along world Z (foot ball / toe): there the up axis
    # would be parallel to the bone, so fall back to "yup" -- which still
    # lands the roll axis on the chosen one.
    up_axis = [a for a in "xyz" if a not in (primary, secondary)][0]
    oj = primary + up_axis + secondary
    end = root
    kids = mc.listRelatives(root, allDescendents=True, type="joint")
    if kids:
        end = kids[0]
    d = [a - b for a, b in zip(mc.xform(end, q=True, ws=True, t=True),
                               mc.xform(root, q=True, ws=True, t=True))]
    sao = "yup" if abs(d[2]) > max(abs(d[0]), abs(d[1])) else "zup"
    mc.select(root)
    mc.joint(edit=True, orientJoint=oj, secondaryAxisOrient=sao,
             children=True, zeroScaleOrient=True)
    for jnt in mc.listRelatives(root, allDescendents=True, type="joint") or [root]:
        if not mc.listRelatives(jnt, children=True, type="joint"):
            mc.setAttr(jnt + ".jointOrient", 0, 0, 0)


# bend joint -> (end joint whose swing defines the bend, desired world Z
# direction of that swing under knee-forward / elbow-back convention)
BEND_JOINTS = {
    "Calf": ("Ankle", -1.0),    # knee vertex forward: ankle swings back
    "Elbow": ("Wrist", +1.0),   # elbow vertex back: wrist swings forward
}


def _set_preferred_angles(roots, primary="x"):
    """Straight chains give IK no bend direction; find it empirically.

    For each bend joint, test-rotate each candidate axis/sign by 10 deg,
    measure which way the end joint's world Z moves, keep the combination
    matching the anatomical convention, and store it as the preferred
    angle. Duplicated IK chains inherit it.
    """
    joints = set()
    for root in roots:
        joints.add(root.split("|")[-1])
        for j in mc.listRelatives(root, allDescendents=True, type="joint") or []:
            joints.add(j.split("|")[-1])
    for part, (end_part, want_z) in BEND_JOINTS.items():
        for j in sorted(joints):
            if not j.endswith("_{}_BN_JNT".format(part)):
                continue
            side = j.split("_")[0]
            end = "{}_{}_BN_JNT".format(side, end_part)
            if end not in joints:
                continue
            # the USER'S placed bend wins: a bent guide chain leaves its
            # bend in the joint orient, and that is the direction the knee
            # or elbow should keep folding
            # candidate bend axes: the two that are not the aim axis
            axes = [a.upper() for a in "xyz" if a != primary]
            jos = [mc.getAttr("{}.jointOrient{}".format(j, a)) for a in axes]
            if any(abs(v) > 1.0 for v in jos):
                axis, jo = max(zip(axes, jos), key=lambda t: abs(t[1]))
                sign = 1.0 if jo > 0 else -1.0
                amount = max(20.0, abs(jo))
                mc.setAttr("{}.preferredAngle{}".format(j, axis),
                           sign * amount)
                logger.debug("preferred angle %s.%s = %s (from placed bend)",
                             j, axis, sign * amount)
                continue
            # truly straight chain: fall back to the anatomical convention,
            # found empirically (test-rotate, measure the end's world swing)
            base = mc.xform(end, q=True, ws=True, t=True)
            best = None
            for axis in axes:
                for sign in (1.0, -1.0):
                    mc.setAttr("{}.rotate{}".format(j, axis), sign * 10.0)
                    moved = mc.xform(end, q=True, ws=True, t=True)
                    mc.setAttr("{}.rotate{}".format(j, axis), 0.0)
                    dz = moved[2] - base[2]
                    score = dz * want_z
                    if best is None or score > best[0]:
                        best = (score, axis, sign)
            _, axis, sign = best
            mc.setAttr("{}.preferredAngle{}".format(j, axis), sign * 20.0)
            logger.debug("preferred angle %s.%s = %s (convention)",
                         j, axis, sign * 20.0)


def _add_twist_joints(roots, count, primary="x"):
    """Undriven twist BN joints, interior-spaced along each limb segment.

    Part of the SKELETON: they exist (and get skinned, mirrored, exported)
    regardless of which mechanism Body Setup later drives them with.
    """
    made = []
    if count < 1:
        return made
    joints = set()
    for root in roots:
        joints.add(root.split("|")[-1])
        for j in mc.listRelatives(root, allDescendents=True, type="joint") or []:
            joints.add(j.split("|")[-1])
    for module, top_part, child_part, label in TWIST_SEGMENTS:
        for j in sorted(joints):
            if not j.endswith("_{}_BN_JNT".format(top_part)):
                continue
            side = j.split("_")[0]
            child = "{}_{}_BN_JNT".format(side, child_part)
            if child not in joints:
                continue
            aim = "translate" + primary.upper()
            seg_len = mc.getAttr(child + "." + aim)
            radius = mc.getAttr(j + ".radius") * 0.8
            for i in range(count):
                # module in the name: which system owns this joint is
                # readable in the outliner and greppable by Body Setup
                name = "{}_{}_{}Twist{}_BN_JNT".format(
                    side, module, label, i + 1)
                if mc.objExists(name):
                    continue
                tj = mc.createNode("joint", name=name, parent=j)
                mc.setAttr(tj + "." + aim,
                           seg_len * (i + 1) / float(count + 1))
                mc.setAttr(tj + ".radius", radius)
                made.append(name)
    return made


def build_skeleton(guides_group="Guides", twist_joints=2,
                   primary_axis="x", secondary_axis="z"):
    if scene_meta.done("skeleton"):
        existing = scene_meta.find("skeleton", name_fallback="BN_Skeleton")
        _, nxt = scene_meta.next_step()
        mc.warning("Skeleton already built ({}). Next step: {}".format(
            existing, nxt or "see Scene Status"))
        if existing:
            mc.select(existing)
        return existing

    found = scene_meta.find("guides", name_fallback=guides_group)
    if not found:
        mc.warning("No '{}' group found. Run the guides phase first "
                   "(action: guides).".format(guides_group))
        return None
    guides_group = found

    roots = []
    for guide_root in _chain_roots(guides_group):
        if guide_root.split("|")[-1].endswith("_GD"):
            continue
        jnt_root = _build_joints(guide_root, None)
        if jnt_root:
            _orient_chain(jnt_root, primary_axis, secondary_axis)
            roots.append(jnt_root)

    _set_preferred_angles(roots, primary_axis)
    twists = _add_twist_joints(roots, twist_joints, primary_axis)

    # assembly: thighs under pelvis, clavicles under chest
    top = "BN_Skeleton" if mc.objExists("BN_Skeleton") else mc.group(empty=True, name="BN_Skeleton")
    for root in roots:
        short = root.split("|")[-1]
        target = top
        if "Thigh" in short and mc.objExists("Pelvis_BN_JNT"):
            target = "Pelvis_BN_JNT"
        elif "Clavicle" in short and mc.objExists("Chest_BN_JNT"):
            target = "Chest_BN_JNT"
        if (mc.listRelatives(root, parent=True) or [None])[0] != target:
            try:
                mc.parent(root, target)
            except RuntimeError:
                pass
    for spine_root in ("Pelvis_BN_JNT",):
        if mc.objExists(spine_root) and not mc.listRelatives(spine_root, parent=True):
            mc.parent(spine_root, top)

    mc.setAttr(guides_group + ".visibility", 0)
    scene_meta.record("skeleton", nodes=[top],
                      info={"chains": len(roots),
                            "twist_joints": twist_joints,
                            "twists_made": len(twists)})
    mc.select(top)
    logger.info("Skeleton built under %s (%d chains)", top, len(roots))
    return top
