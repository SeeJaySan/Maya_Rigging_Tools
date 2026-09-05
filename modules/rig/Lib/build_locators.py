"""Build guides for a biped: spine, arm, and leg chains.

Creates a "Guides" group with named transform hierarchies matching the
repo naming convention ({side}_Name_BN). A guide is a plain transform
tagged with an `isGuide` attribute; its only shapes are the RGB axis
arrows, so clicking an arrow selects the guide. Positions are rough biped defaults in
centimeters.

Library module: run through the Build Biped workflow tool (guides phase),
not from the toolset UI directly.
"""

import logging

import maya.cmds as mc

from modules.rig.Lib import scene_meta

logger = logging.getLogger(__name__)

SPINE_GUIDES = [
    ("Pelvis_BN", (0, 95, 0)),
    ("Spine1_BN", (0, 105, 0)),
    ("Spine2_BN", (0, 115, 0)),
    ("Spine3_BN", (0, 125, 0)),
    ("Chest_BN", (0, 135, 0)),
]

# limb chains are STRAIGHT lines: rotate the chain root and the whole
# limb aims as one piece. IK bend direction comes from preferred angles
# set at skeleton-build time, not from pre-bent guides.
ARM_GUIDES = [
    ("Clavicle_BN", (4, 142, 0)),
    ("Shoulder_BN", (16, 142, 0)),
    ("Elbow_BN", (42, 142, 0)),
    ("Wrist_BN", (68, 142, 0)),
]

LEG_GUIDES = [
    ("Thigh_BN", (10, 92, 0)),
    ("Calf_BN", (10, 50, 0)),
    ("Ankle_BN", (10, 10, 0)),
    ("Ball_BN", (10, 2, 12)),
    ("Toe_BN", (10, 2, 20)),
]

# Foot PIVOT guides (_GD suffix: placement targets for the reverse foot's
# pivots, NOT bones - the skeleton builder skips them). Heel is where the
# foot rocks back; the bank guides are the inner/outer sole edges the foot
# tips over sideways.
FOOT_PIVOT_GUIDES = [
    ("Heel_GD", (10, 0, -7)),
    ("BankInner_GD", (6, 0, 12)),
    ("BankOuter_GD", (14, 0, 12)),
]




# The R_ guides are not placed by hand: they live under a group scaled -1
# in X and have every transform connected straight from their L_ twin, so
# the right side updates the instant a left guide is moved or rotated.
MIRROR_GROUP = "Guides_Mirror"
MIRROR_ATTRS = ("translate", "rotate", "scale")


GUIDE_ATTR = "isGuide"


def make_guide(name):
    """A guide: bare transform tagged isGuide, arrows as its shapes."""
    node = mc.createNode("transform", name=name, skipSelect=True)
    tag_guide(node)
    _add_axis_tripod(node)
    return node


def tag_guide(node):
    if not mc.attributeQuery(GUIDE_ATTR, node=node, exists=True):
        mc.addAttr(node, longName=GUIDE_ATTR, attributeType="bool",
                   defaultValue=True)
        mc.setAttr(node + "." + GUIDE_ATTR, lock=True)


def is_guide(node):
    """Guides are recognised by the isGuide tag (legacy: a locator shape)."""
    if mc.attributeQuery(GUIDE_ATTR, node=node, exists=True):
        return True
    return bool(mc.listRelatives(node, shapes=True, type="locator"))


def guides_under(top):
    return [n for n in (mc.listRelatives(top, allDescendents=True,
                                         type="transform",
                                         fullPath=True) or [])
            if is_guide(n)]


def _build_chain(guides, parent_group, prefix="", mirror=False):
    previous = None
    top = None
    for name, pos in guides:
        loc = make_guide(prefix + name)
        x = -pos[0] if mirror else pos[0]
        mc.xform(loc, ws=True, t=(x, pos[1], pos[2]))
        if previous:
            mc.parent(loc, previous)
        else:
            top = loc
        previous = loc
    mc.parent(top, parent_group)
    return top


def build_guides(side="L"):
    if mc.objExists("Guides"):
        top = "Guides"
    else:
        top = mc.createNode("transform", name="Guides")

    mirror = side.upper().startswith("R")
    prefix = "{}_".format(side.upper()[0])

    made = []
    if not mc.objExists("Pelvis_BN"):
        made.append(_build_chain(SPINE_GUIDES, top))
    made.append(_build_chain(ARM_GUIDES, top, prefix=prefix, mirror=mirror))
    made.append(_build_chain(LEG_GUIDES, top, prefix=prefix, mirror=mirror))
    made += ensure_foot_pivot_guides(side, top)
    orient_guides(top)

    made += mirror_guides(top)[0]
    scene_meta.record("guides", nodes=[top], info={"side": side})
    mc.select(top)
    logger.debug("Built guides: %s", made)
    return made


def _mirror_group(top="Guides"):
    """The -1 scaleX group the driven R_ chains live under."""
    if mc.objExists(MIRROR_GROUP):
        return MIRROR_GROUP
    grp = mc.createNode("transform", name=MIRROR_GROUP)
    mc.setAttr(grp + ".scaleX", -1)
    grp = mc.parent(grp, top, relative=True)[0]
    for attr in ("tx", "ty", "tz", "rx", "ry", "rz", "sx", "sy", "sz"):
        mc.setAttr(grp + "." + attr, lock=True)
    return grp


def is_driven(loc):
    """True for a guide whose placement comes from its mirror twin."""
    return bool(mc.listConnections(loc + ".translate", source=True,
                                   destination=False, plugs=True))


def _free_transform(node):
    """Unlock and disconnect a node's transform so it can be edited."""
    for attr in MIRROR_ATTRS:
        for axis in ("",) + tuple("XYZ"):
            plug = "{}.{}{}".format(node, attr, axis)
            mc.setAttr(plug, lock=False)
        for src in mc.listConnections(node + "." + attr, source=True,
                                      destination=False, plugs=True) or []:
            mc.disconnectAttr(src, node + "." + attr)


def _drive_mirror(src, dst):
    """Wire dst's local transform to src's. The group's -1 scaleX turns the
    identical local values into a true mirror across YZ, which also gives
    the R_ joints mirrored BEHAVIOUR when the skeleton is built from them.
    """
    _free_transform(dst)
    for attr in MIRROR_ATTRS:
        if not mc.isConnected(src + "." + attr, dst + "." + attr):
            mc.connectAttr(src + "." + attr, dst + "." + attr, force=True)
        mc.setAttr(dst + "." + attr, lock=True)


def mirror_guides(top="Guides"):
    """Build (or adopt) the R_ guides and drive them from the L_ side.

    Safe to re-run: missing R_ chains are created, R_ chains left loose by
    an older build are moved under the mirror group, and every connection
    is refreshed. Returns (made, linked).
    """
    grp = _mirror_group(top)
    made = []

    def adopt(name):
        parent = (mc.listRelatives(name, parent=True, fullPath=True)
                  or [None])[0]
        if parent and parent.split("|")[-1] == MIRROR_GROUP:
            return
        # a locked or connected transform cannot be reparented, so free it
        # first: this is the migration path for guides built before the
        # mirror group existed
        _free_transform(name)
        mc.parent(name, grp)

    for guides in (ARM_GUIDES, LEG_GUIDES):
        root = "R_" + guides[0][0]
        if mc.objExists(root):
            adopt(root)
        else:
            made.append(_build_chain(guides, grp, prefix="R_"))
    for name, pos in FOOT_PIVOT_GUIDES:
        full = "R_" + name
        if mc.objExists(full):
            adopt(full)
            continue
        loc = make_guide(full)
        mc.xform(loc, ws=True, t=pos)
        made.append(mc.parent(loc, grp)[0])

    linked = 0
    for dst in guides_under(grp):
        src = "L_" + dst.split("|")[-1][2:]
        if mc.objExists(src):
            _drive_mirror(src, dst)
            linked += 1
    logger.debug("mirror guides: %d built, %d driven", len(made), linked)
    return made, linked


AXIS_TRIPOD = (
    ("X", (1, 0, 0), 13, (1.0, 0.10, 0.10)),   # red = bone direction
    ("Y", (0, 1, 0), 14, (0.15, 0.90, 0.20)),  # green
    ("Z", (0, 0, 1), 6, (0.15, 0.35, 1.0)),    # blue = front reference
)


AXIS_SHAFT = 0.045      # shaft radius as a fraction of axis length
AXIS_HEAD = 0.28        # cone length as a fraction of axis length


def _axis_shader(rgb):
    """Shared unlit shader per axis color (created once, reused)."""
    name = "guideAxis_{}_{}_{}_SS".format(*[int(round(c * 100)) for c in rgb])
    sg = name + "SG"
    if not mc.objExists(sg):
        shader = mc.shadingNode("surfaceShader", asShader=True, name=name)
        mc.setAttr(shader + ".outColor", *rgb, type="double3")
        sg = mc.sets(name=sg, renderable=True, noSurfaceShader=True,
                     empty=True)
        mc.connectAttr(shader + ".outColor", sg + ".surfaceShader",
                       force=True)
    return sg


AXIS_ATTR = "guideAxisLength"   # on TOOLSET_META: arrow length, live
WIDTH_ATTR = "guideAxisWidth"   # on TOOLSET_META: shaft/head thickness, live
LEN_MD = "guideAxisLen_MD"      # X shaft length, Y head length, Z head offset
RAD_MD = "guideAxisRad_MD"      # X shaft radius, Y head radius, Z shaft offset


def _meta_float(attr, default):
    n = scene_meta.node()
    if not mc.attributeQuery(attr, node=n, exists=True):
        mc.addAttr(n, longName=attr, attributeType="double",
                   minValue=0.01, defaultValue=default, keyable=True)
    return n + "." + attr


def axis_size_plug():
    """Scene-wide arrow LENGTH. Every guide's arrows follow it live,
    no rebuild. Set from Build Biped or the channel box on TOOLSET_META."""
    return _meta_float(AXIS_ATTR, 3.0)


def axis_width_plug():
    """Scene-wide arrow THICKNESS, independent of length."""
    return _meta_float(WIDTH_ATTR, 3.0)


def _axis_math():
    """Two shared multiplyDivide nodes turn length/width into every
    dimension the arrow history nodes need. Created once per scene."""
    if not mc.objExists(LEN_MD):
        ln = mc.createNode("multiplyDivide", name=LEN_MD, skipSelect=True)
        length = axis_size_plug()
        for ch in "XYZ":
            mc.connectAttr(length, "{}.input1{}".format(ln, ch))
        mc.setAttr(ln + ".input2X", 1.0 - AXIS_HEAD)
        mc.setAttr(ln + ".input2Y", AXIS_HEAD)
        mc.setAttr(ln + ".input2Z", 1.0 - AXIS_HEAD * 0.5)
    if not mc.objExists(RAD_MD):
        rd = mc.createNode("multiplyDivide", name=RAD_MD, skipSelect=True)
        width = axis_width_plug()
        mc.connectAttr(width, rd + ".input1X")
        mc.connectAttr(width, rd + ".input1Y")
        mc.connectAttr(axis_size_plug(), rd + ".input1Z")
        mc.setAttr(rd + ".input2X", AXIS_SHAFT)
        mc.setAttr(rd + ".input2Y", AXIS_SHAFT * 2.6)
        mc.setAttr(rd + ".input2Z", (1.0 - AXIS_HEAD) * 0.5)
    return LEN_MD, RAD_MD


def _style_shape(shape, color, rgb):
    # Wireframe color from the index override; SHADED color needs a real
    # shader (display overrides leave a shaded mesh grey). surfaceShader is
    # unlit, so the axis reads as a pure flat color in any lighting.
    mc.setAttr(shape + ".overrideEnabled", 1)
    mc.setAttr(shape + ".overrideRGBColors", 1)
    mc.setAttr(shape + ".overrideColorRGB", *rgb)
    mc.setAttr(shape + ".overrideColor", color)
    mc.setAttr(shape + ".castsShadows", 0)
    mc.setAttr(shape + ".receiveShadows", 0)
    mc.sets(shape, edit=True, forceElement=_axis_shader(rgb))


def _live_piece(guide, name, vec, primitive, len_plug, off_plug, rad_plug,
                color, rgb):
    """One arrow piece (shaft or head) as a shape ON the guide, with its
    poly history kept and driven: primitive size from the meta attrs, a
    polyMoveVertex sliding it out along the axis so the base sits at the
    guide. Returns the shape."""
    kwargs = dict(name=name, axis=vec, subdivisionsAxis=12,
                  subdivisionsHeight=1, constructionHistory=True)
    if primitive == "cylinder":
        xform, hist = mc.polyCylinder(subdivisionsCaps=0, **kwargs)
    else:
        xform, hist = mc.polyCone(subdivisionsCap=0, **kwargs)
    mc.connectAttr(len_plug, hist + ".height")
    mc.connectAttr(rad_plug, hist + ".radius")
    mv = mc.polyMoveVertex(xform, constructionHistory=True)[0]
    along = "XYZ"[list(vec).index(1)]
    mc.connectAttr(off_plug, "{}.translate{}".format(mv, along))
    shape = mc.listRelatives(xform, shapes=True, fullPath=True)[0]
    shape = mc.rename(shape, name + "Shape")
    _style_shape(shape, color, rgb)
    mc.parent(shape, guide, relative=True, shape=True)
    mc.delete(xform)
    return "{}|{}Shape".format(guide, name)


def _add_axis_tripod(loc, size=None, width=None):
    """RGB axis arrows as SHAPES on the guide transform: clicking an arrow
    selects the guide. Each arrow is a shaft cylinder + head cone whose
    poly history stays live and is driven from TOOLSET_META, so length
    and thickness change scene-wide without a rebuild."""
    short = loc.split("|")[-1]
    if mc.objExists("{}_axisXShape".format(short)):
        return
    ln, rd = _axis_math()
    for label, vec, color, rgb in AXIS_TRIPOD:
        base = "{}_axis{}".format(short, label)
        _live_piece(loc, base, vec, "cylinder",
                    ln + ".outputX", rd + ".outputZ", rd + ".outputX",
                    color, rgb)
        _live_piece(loc, base + "Head", vec, "cone",
                    ln + ".outputY", ln + ".outputZ", rd + ".outputY",
                    color, rgb)
    if size is not None:
        mc.setAttr(axis_size_plug(), float(size))
    if width is not None:
        mc.setAttr(axis_width_plug(), float(width))


def set_axis_size(size=3.0, width=None, top="Guides"):
    """Set the scene-wide arrow length (and optionally thickness). Every
    tripod follows live."""
    mc.setAttr(axis_size_plug(), float(size))
    if width is not None:
        mc.setAttr(axis_width_plug(), float(width))
    return size


def _aim_guide(loc, target_pos):
    """Orient a guide so +X points at the target (one-shot, no constraint:
    rotating a chain root still aims the whole limb by hand)."""
    pos = mc.xform(loc, q=True, ws=True, t=True)
    d = [target_pos[i] - pos[i] for i in range(3)]
    length = (d[0] ** 2 + d[1] ** 2 + d[2] ** 2) ** 0.5
    if length < 1e-5:
        return
    d = [v / length for v in d]
    # front reference is world +Z unless the bone runs along Z (toes)
    up = (0.0, 1.0, 0.0) if abs(d[2]) > 0.9 else (0.0, 0.0, 1.0)
    tmp = mc.group(empty=True, world=True)
    mc.xform(tmp, ws=True, t=target_pos)
    cons = mc.aimConstraint(tmp, loc, aimVector=(1, 0, 0), upVector=up,
                            worldUpType="vector", worldUpVector=up)
    mc.delete(cons)
    mc.delete(tmp)


def orient_guides(top="Guides"):
    """Aim every guide down its chain; leaves and pivot guides copy the
    parent's frame. Safe to re-run: refreshes axes after guides move.

    Rotating a parent MOVES its children, so all world positions are
    snapshotted first and re-imposed top-down after each rotation - the
    naive aim-in-place version scrambled every placed guide (caught live;
    positions had to be rebuilt from the skeleton).
    """
    if not mc.objExists(top):
        return 0
    all_locs = []
    for loc in guides_under(top):
        # driven mirrors take their frame from the twin: aiming them here
        # would just fail on the locked, connected transforms
        if is_driven(loc):
            continue
        all_locs.append(loc)
    all_locs.sort(key=lambda n: n.count("|"))          # parents first
    snapshot = {loc: mc.xform(loc, q=True, ws=True, t=True)
                for loc in all_locs}

    def child_locs(loc):
        return [c for c in (mc.listRelatives(loc, children=True,
                                             type="transform",
                                             fullPath=True) or [])
                if is_guide(c)]

    count = 0
    for loc in all_locs:
        kids = child_locs(loc)
        if kids:
            _aim_guide(loc, snapshot[kids[0]])
        else:
            parent = (mc.listRelatives(loc, parent=True, fullPath=True)
                      or [None])[0]
            if parent in snapshot:
                mc.xform(loc, ws=True,
                         ro=mc.xform(parent, q=True, ws=True, ro=True))
        # the rotation displaced every descendant: re-impose their
        # snapshotted world positions (top-down order keeps this stable)
        mc.xform(loc, ws=True, t=snapshot[loc])
        for kid in kids:
            mc.xform(kid, ws=True, t=snapshot[kid])
        count += 1
    # final pass: everything back at its snapshot, exactly
    for loc in all_locs:
        mc.xform(loc, ws=True, t=snapshot[loc])
    return count


def upgrade_guide_display(top="Guides", size=3.0, width=None):
    """Retrofit guides to the current display: tag them, drop legacy
    locator shapes and old arrow layouts, rebuild live arrows, set the
    scene-wide size, then orient everything."""
    added = 0
    for loc in guides_under(top):
        short = loc.split("|")[-1]
        tag_guide(loc)
        if mc.objExists("{}_axisXHeadShape".format(short)):
            continue
        # legacy: locator crosshair, baked arrow shapes, or per-arrow
        # scaled children. Clear them all and rebuild as live shapes.
        for shape in mc.listRelatives(loc, shapes=True, fullPath=True) or []:
            sh = shape.split("|")[-1]
            if mc.nodeType(shape) == "locator" or "_axis" in sh:
                mc.delete(shape)
        for child in mc.listRelatives(loc, children=True, type="transform",
                                      fullPath=True) or []:
            if child.split("|")[-1].startswith(short + "_axes"):
                mc.delete(child)
        _add_axis_tripod(loc)
        added += 1
    set_axis_size(size, width, top)
    oriented = orient_guides(top)
    return added, oriented


def straighten_limb_guides(side="L"):
    """Project each limb's mid guide onto the root->end line, keeping the
    user's endpoint placement: the chain becomes a straight line that can
    be rotated in place as one piece."""
    def project(root, mid, end):
        if not all(mc.objExists(n) for n in (root, mid, end)):
            return False
        a = mc.xform(root, q=True, ws=True, t=True)
        b = mc.xform(mid, q=True, ws=True, t=True)
        c = mc.xform(end, q=True, ws=True, t=True)
        d = [c[i] - a[i] for i in range(3)]
        l2 = sum(v * v for v in d) or 1.0
        t = sum((b[i] - a[i]) * d[i] for i in range(3)) / l2
        mc.xform(mid, ws=True, t=[a[i] + d[i] * t for i in range(3)])
        # end is a CHILD of mid in the guide chain, so moving mid dragged
        # it: restore its snapshot or the line we projected onto is gone
        mc.xform(end, ws=True, t=c)
        return True
    pre = "{}_".format(side.upper()[0])
    done = []
    if project(pre + "Thigh_BN", pre + "Calf_BN", pre + "Ankle_BN"):
        done.append("leg")
    if project(pre + "Shoulder_BN", pre + "Elbow_BN", pre + "Wrist_BN"):
        done.append("arm")
    return done


def ensure_foot_pivot_guides(side="L", top="Guides"):
    """Add the foot pivot guides for a side if missing (safe to re-run,
    including on scenes whose guides predate these)."""
    mirror = side.upper().startswith("R")
    prefix = "{}_".format(side.upper()[0])
    if not mc.objExists(top):
        top = mc.createNode("transform", name=top)
    made = []
    for name, pos in FOOT_PIVOT_GUIDES:
        full = prefix + name
        if mc.objExists(full):
            continue
        loc = make_guide(full)
        x = -pos[0] if mirror else pos[0]
        mc.xform(loc, ws=True, t=(x, pos[1], pos[2]))
        loc = mc.parent(loc, top)[0]
        made.append(loc)
    return made
