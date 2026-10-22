"""One pinned kicad-route attempt inside the networkless sandbox (AGENTEE-93).

This is the in-sandbox half of the routing adapter. It imports only the
standard library, so the coordinator can run it as
``/usr/bin/python3 -I -B /opt/agent-ee-routing/kicad_route_attempt.py`` from a
read-only script mount whose bytes it hashes. Exact net binding reuses the
shared net_binding and symbol_library modules, mounted read-only beside it.

The adapter checks kicad-route's versioned result contract and the mounted
code byte for byte against one qualified pin. Unknown schemas and changed
code are refused. The router's argv and environment come from constants and
coordinator-declared arguments matched against fixed patterns; nothing the
writer controls reaches them, and ``--backend-env``, ``--force``, ``--canary``
and ``--rev`` are never passed. ``--backend-arg`` carries only KRT's
``--layers``/``--layer-costs`` built from a declared layer policy (AGENTEE-231).
``--experiment-policy`` names only the adapter-written five-field
routability-policy/1 file (AGENTEE-442).

The report is provenance. It never makes a check pass: acceptance comes only
from agent-ee's own fresh checks on the captured proposal.
"""

import collections
import errno
import hashlib
import json
import math
import os
import posixpath
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath

PIN = 'b5774496d8466b67ffa0362b6665fee2108155a3'
# SHA-256 of every tracked bin/ and src/ blob at PIN, including the owned engine.
PINNED_CODE = {'bin/kicad-route': '4d01520182f391ff1f92be7eb1e2415d3675a309a38ef449012d49e2d03e42e9',
 'src/kicad_route/__init__.py': '23611cbf0bfd79c20f1e18fa402bbe8c1bcd63b412fbf84e7eb3b5f3c0062af4',
 'src/kicad_route/__main__.py': 'ae74743797f08c890cf668ae24c08125c899e4b0940873f64352552a2c94351e',
 'src/kicad_route/backend_names.py': '584e705ee74e2a1f60066ef5f91ecc6add08b0a9b83ddfc120694a4fe3b967c4',
 'src/kicad_route/barrier.py': 'de34c57c391248c4fdb02fdd9f71d6736edbc15621350be1ff188bc8b217df6c',
 'src/kicad_route/baselinecache.py': '3a36d87ddcdc19fdae6c330e51202ca091bf59e4b1ee583a9c8b4ff9c8eb202f',
 'src/kicad_route/blocked.py': 'b2dc298ca38dd994479afdb6ced91d8196b0fd1626f2828c0996135ecb57c7f5',
 'src/kicad_route/campaign.py': 'c70e888fa40f7e0605fc14b8656ccd34a5e977a295f71c348876ed9dd40e75a9',
 'src/kicad_route/canary.py': '0384d1722889e207fcc544e9df65164920aa3cc153df5228349b42cd690a7243',
 'src/kicad_route/cli.py': 'd43569e4d81a4355712d356efec35ec40167f3318c9155c8172792164dcd01b1',
 'src/kicad_route/common.py': '4fd09eef7547c947700067975a54e08c8e6b3ed8d3c5ac94cf8ae77e4a593038',
 'src/kicad_route/configuration_evidence.py': '08f81a88c9d911d474c45515cad1303bbf7eb6f15438f6c7c278d69c6e075d59',
 'src/kicad_route/contract.py': '5f6c7acd40138f5f13d140bcdda7539aacf1edcd011daa8f36a76e72663bb698',
 'src/kicad_route/copper_ownership.py': 'bd1e0dd79c62ca654d7133b3f1693e1ac8e1b27c1646ebe6227c71b4bf532b07',
 'src/kicad_route/corpus.py': '7ec0dfdd8cd3baf33fc3536f36539af0706a0f21f469ddf8efee4fff100be41d',
 'src/kicad_route/decoupling.py': '29724e237cc94c0af783619991dc5659b4b582eb6cdb33fd0d59542a108232f6',
 'src/kicad_route/determinism.py': 'c05ab76de1e24be3f9eeb66807e1f739ed722025e55b65481d877c56dd683abc',
 'src/kicad_route/diff_pairs.py': '6a9025e0d547eb22fe645cb3da5b6887ae650f884238992ca6c568c5477fb708',
 'src/kicad_route/engine.py': '4f4451f6356a9b5ba63b01d4e1d97f7490b80deb0a1518e28b29e9ae13c9d1c1',
 'src/kicad_route/evidence.py': 'ae818865ecdd317abe9c82129782217817df4ae75548d4b9d85b08cda3b99881',
 'src/kicad_route/experiment_policy.py': '575e7aef8a2ba825ef05211603b16e00c05ebf4439e2976ef0a7884bd330265d',
 'src/kicad_route/floor.py': '2f954a2d1142c4f68d7e2904781f4470c0976af925a0a91c31c8de26d4f31ae6',
 'src/kicad_route/installation.py': 'f3aa9698bd2c5b63e8e3933f300c17b8cb59dedca41ee21031f1690eda5e88b7',
 'src/kicad_route/kicad/barrier.py': 'ffeab974f15db240d55c1d3e8a19450d885be7ab33b7f595fdf79c5f6e2409e4',
 'src/kicad_route/kicad/decoupling.py': '498624e3f38485519865bb871a01e3f62fc3a41c9bde80d2966ae3c05f7e88b5',
 'src/kicad_route/kicad/facts.py': 'd47a3fd2eb40518b7beba5457b679b901150c64e3988f86537d67f373bef8c52',
 'src/kicad_route/kicad/locks.py': '5b15d4ddfbec9b7e32a841a16f9c1dd383b043c240a293251737e9cba218800a',
 'src/kicad_route/kicad/native_components.py': '071b5624884586e9a8ea83b7b253c292267b352623f58c3291ddd1ae7ec14fb6',
 'src/kicad_route/kicad/pairs.py': '44763bfd3b25871cf9c5b5636d97f4534157920ac83ca755c3d57628638552f9',
 'src/kicad_route/kicad/planes.py': '8498b1566edda7d29631210435669737a22d990df7fbd3c487c92a6827f5f07d',
 'src/kicad_route/kicad/pour.py': 'a49f64c51854720e8ea680d81b94969dc8242da0c3ee93b72c8948a57b908c14',
 'src/kicad_route/kicad/remove_owned.py': '361c2520af37ee6c7a6d39b66054d2e14ee6e77381defe4d62c5fa93d4ae2dfe',
 'src/kicad_route/kicad/rename_nets.py': 'a51c1767c0ce1d0edf180700a026d695678228d56ece4bab688d9bfd3d23d2a8',
 'src/kicad_route/kicad/specctra.py': '0cd5031726ab48c7b3c2bf1dd3978837574d7452d7a79dda154123acb1aeb9d1',
 'src/kicad_route/kicad/spokes.py': 'c419ea1123a6de2588358aa58d0dfaabd4d5f55d50d559023ce48d2271f12bd9',
 'src/kicad_route/ledger.py': '267c90f9c8c54b18cfad68ed00ebe0013018742dac353a43f8e657dfd5eb366d',
 'src/kicad_route/native_components.py': 'fc5099d821f972212b0a2e30bfb15b9755336684902cea933d6db88446b361e0',
 'src/kicad_route/netnames.py': '731bcf61a909ef2720fd90b074f6cfb991a1c6ea6bea7fecf5ba3cb7fc47304f',
 'src/kicad_route/plane_plan.py': 'fab213cc155347af3c36bda5694b7aa871058c6f8b77eaf52ebd2b209bfb2d80',
 'src/kicad_route/planes.py': 'ad1e35f16ae34e3efe9accb7dc6505ada6e35431e30dfcca4111ad2df661df61',
 'src/kicad_route/pour.py': '1583883ee1682bf0f72d709b1e146384d37b481a423584de9bd564ac18f1b6b7',
 'src/kicad_route/procgroup.py': 'd843c9b334874d0334c5ea0e9a58d83d22581648e915d40f13a6d8c8b595a3d5',
 'src/kicad_route/provenance.py': '0873c67d36c14a71eb9428658bd76d9c073282134eda4f2db0d00b17a161282e',
 'src/kicad_route/publication.py': '5affc73aef8c78df071c5a9fbce6184be357b0ab8617dcf171f33e28dd5669fd',
 'src/kicad_route/router/.claude/skills/diagnose-routing-failures/SKILL.md': '0abb55757415b58f1c45aae2b0c9c09368541cb5611c2621e362ef15f5349b14',
 'src/kicad_route/router/.claude/skills/review-routed-board/SKILL.md': '29e7c0e3a11ead24f35e17abf1a1acbca30f8d33d00ba06aca41d4a92c4c0f20',
 'src/kicad_route/router/LICENSE': '6df0be9100e852f1749d2e88df423b0f71cab5bff7af35f749e10cf644911390',
 'src/kicad_route/router/UPSTREAM.json': '073bbeea8dd8b20b396347647949375f6439daa6333f5d1b8567ec268a2d6db9',
 'src/kicad_route/router/VERSION': 'e14c67c5f4dd36234ef4bc5ba5ef48d5d79e27bad4038c9bc8a64714566aa06a',
 'src/kicad_route/router/__init__.py': 'ade21c465d05d47c6c4362de7216e025520a4574f69141d0a70fa5798a0ab551',
 'src/kicad_route/router/build_router.py': 'f462c2c94e93effe0ffe82bb2394822277317f2847a416daf968fd966c465fdb',
 'src/kicad_route/router/krt_capabilities.py': '14cbaa11069bdf789d7999d0cbd44612195a34ee10ce3da2133c61451e953d86',
 'src/kicad_route/router/py_placer/_path.py': 'c93bbd159ada44096f7313267e85b68533b7e71674c290d9eca869c976fb4dde',
 'src/kicad_route/router/py_placer/place_route_loop.py': '3a1c54a1bb9d5f97e8291d9a66d22db73b3382ea756a63d0c8a621ad677a9810',
 'src/kicad_route/router/py_placer/placement/__init__.py': 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855',
 'src/kicad_route/router/py_placer/placement/arrays.py': '5a37d73a19a1854960742ec70b7f2887260ed6b8c32942667db01932a1330539',
 'src/kicad_route/router/py_placer/placement/board_grid.py': '9732d3d0c06bffe277b3a4899158579a221858dfa1266edb7368adac4fc0090c',
 'src/kicad_route/router/py_placer/placement/body.py': '5199c05c5bf89693d058cf92d899e0b55e597021e0dc44847e0169045e158c90',
 'src/kicad_route/router/py_placer/placement/cli_gates.py': 'fd9c9ab4f8f2007ec1b8593e52147c32a857c43ebfb33fa809f7013196fde5e5',
 'src/kicad_route/router/py_placer/placement/connector_geometry.py': '0db0e1de251cf74114fbbd0c288022279000b672671bdbaa143871790c950b66',
 'src/kicad_route/router/py_placer/placement/design_brief.py': 'd540cdaf8c1e7ac8f6f86ca9be3a36cb69d8dbd3aaf91826919d9db19f4b4a73',
 'src/kicad_route/router/py_placer/placement/diagnosis.py': 'bfcfa28608efb5cc2128685ba07555f0760fa87e4cedf69a8b79c47f1da603fe',
 'src/kicad_route/router/py_placer/placement/edge_facing.py': 'eaf2166416750cbd79521b393ef735a96ea8c5006541db202c841e448be7c529',
 'src/kicad_route/router/py_placer/placement/emc_roles.py': '727e7a583994897ce04d5f46f76af345e320c84e0568fc13046719c1577caa8f',
 'src/kicad_route/router/py_placer/placement/escape.py': '553af70095728cdbf477249460ba63ba8383ac90ac144c15a289ca7156d6abf8',
 'src/kicad_route/router/py_placer/placement/floorplan.py': '5e212aa6bf6117540fd3e9b11fa65c5616e95221794340c56a536c6835cff7f9',
 'src/kicad_route/router/py_placer/placement/groups.py': '8e588f887d62573485526368b4e963f69436893b68e88eec6c79687911348990',
 'src/kicad_route/router/py_placer/placement/legality.py': '154aaa36d5bc2fbcb9e8a0ec5ea043c01e6527e272d10588538de34a741981b7',
 'src/kicad_route/router/py_placer/placement/lock_advisor.py': '58803c7ed97037fac32fd7a703b32500403df30037b349cc1ae4bcb76bac09d3',
 'src/kicad_route/router/py_placer/placement/mating.py': '9f520de5f509deb719f61ae6768994ae989d369815c29ad597530e96a9c46ea5',
 'src/kicad_route/router/py_placer/placement/options.py': '389ef429555d5891eed31709517a59dada18396d16cd10647e68f4ff982518f9',
 'src/kicad_route/router/py_placer/placement/pair_order.py': '3636f936b7699e44f0348fee7398b3f6b609b7cd930aed5e1a4880945677409a',
 'src/kicad_route/router/py_placer/placement/parser.py': '1af5f80cf19e06ac711379bd722e3b2b4e605a23ba2a05b9f694b5bf1e920bdb',
 'src/kicad_route/router/py_placer/placement/part_class.py': '001f5cfc3b2c952d68395f80aaee6132b5e5c435f399ae1735834fefdbf1d52f',
 'src/kicad_route/router/py_placer/placement/pin_rows.py': '488bd8100321fe20b01c577861d80bb81ace3c18126ea21a86b3c82b5a94ef23',
 'src/kicad_route/router/py_placer/placement/placement_state.py': '26148d3b2cd6648223dd116c7db9d4ad910584260112a6efee9a389aa09dfb2c',
 'src/kicad_route/router/py_placer/placement/portfolio.py': 'b5878857ca58d0e6a85bc3e8a3c829e1dd25ac71a8e42cd94120066eb1363af2',
 'src/kicad_route/router/py_placer/placement/pose_ops.py': 'dda20791574817f61c6f77baf89f6f8331d55ef4d93f92b137a64229dc05a1c1',
 'src/kicad_route/router/py_placer/placement/provenance.py': '250f04eb2ab2cf665b013e2b7f23376d97670cd2da8610dc6d8eb5a2f5fed8c2',
 'src/kicad_route/router/py_placer/placement/quench.py': 'ede844c4d40ea9a9f05e183eb54a6bf94f4074c932b4c741a113f8fc07c55846',
 'src/kicad_route/router/py_placer/placement/reachability.py': '308daeca20825d7dec7c4d07accb7492be9a2ce47a0a21bfcd3b201801e77ba5',
 'src/kicad_route/router/py_placer/placement/reconcile.py': '796a72b1fbab3c15efd966eff6c70842a02c35de095b91a0f627c9c4a1b8ae1e',
 'src/kicad_route/router/py_placer/placement/reconstruct.py': '239736b1f1ee5ef45d6cf003fd2c1021335f35fb157bc093fec7af73807ccdd2',
 'src/kicad_route/router/py_placer/placement/relocate.py': '162a7950ac20aaf6ccfced800f47fda6307b39b0ce3ba23aefd46c2298868c40',
 'src/kicad_route/router/py_placer/placement/replicate.py': 'f87d5048ee02aa6fb0c83fc093ff7ad3bdb83bfa7d63a2c9564a740454690ff8',
 'src/kicad_route/router/py_placer/placement/roles.py': '3b1f4ade3c21000dbc996e8f4df291d6657ae05174903ab37a5adac458e13c67',
 'src/kicad_route/router/py_placer/placement/routability.py': 'cf54662d9b76ccd4d7a5ee1bc693c9f105627604ccdf07c51b5407117b2dd58d',
 'src/kicad_route/router/py_placer/placement/seeder.py': '4f0501d48e8297c74d693e7106fb80af68c7d57f2f1a5c09aeea3106fc7ce907',
 'src/kicad_route/router/py_placer/placement/utility.py': '980546b1022f421210ac7c9cc6630c3f4253eecb8670ec3916ab5944934313b6',
 'src/kicad_route/router/py_placer/placement/writer.py': 'b6d9637c03b18ee0f4419546b95c16f8d48583ddd959acf2e132ed9143507da0',
 'src/kicad_route/router/py_placer/placement_score.py': 'cee8dc53f8b101d30aa9293a7cf7c4863cb0c63304d8307c372f19e9788c3727',
 'src/kicad_route/router/py_placer/pose_score.py': 'f4abe0946fd34652223191bf60ae3ac9cfd0b4aad8025c696e7f386c5df22a83',
 'src/kicad_route/router/py_router/_placer_path.py': '6f7dc70624a51fbe1142ecf29965829105666bbdf8d0ba7e739267810a815c65',
 'src/kicad_route/router/py_router/add_gnd_vias.py': '2887466ca41aa8a0f087a0c099f72983ca25b5be8867e85d0538dbf214beecd6',
 'src/kicad_route/router/py_router/animate_route.py': '236b494d5ca5596933aa428371d88db8f4bba5f4ea7ea74cadf8347d16a2c8bf',
 'src/kicad_route/router/py_router/bga_fanout.py': 'e0bd1c7d1af04cbb33186063cbbac5f02649d25e1061ca94daf2b05991c4261c',
 'src/kicad_route/router/py_router/bga_fanout/__init__.py': 'fd66290a79f3940dabe7a3288481efcd09bdb79693ec1688bbe6ffa1932b4055',
 'src/kicad_route/router/py_router/bga_fanout/collision.py': '07825fd66148dc54676a1070461ebf1e40c0dfe1a2e735cd5788b216da1aa844',
 'src/kicad_route/router/py_router/bga_fanout/constants.py': 'e64e41a82aaeca04c2e261ead8d4ea1718e35c0312d9ddcafb3bed3e6dbdc27e',
 'src/kicad_route/router/py_router/bga_fanout/diff_pair.py': '204ed0b36870a72181a961d4ba23f616405b7b8f853e75118656030f092fb0c8',
 'src/kicad_route/router/py_router/bga_fanout/escape.py': 'be8cd7502e4763472f3fe28c82503dad31cd986c3ba2e7173c7f4e2783f126d1',
 'src/kicad_route/router/py_router/bga_fanout/flip_frame.py': '30bea853f5b8d1d23800c2f6842fa6da9e64b0f6e7bd6803c8159254cec0067e',
 'src/kicad_route/router/py_router/bga_fanout/geometry.py': '83aafff01408d1810eb925313089cd9134d332c3a78350b693d2840809e4b657',
 'src/kicad_route/router/py_router/bga_fanout/grid.py': '3d0ab716440615a06fab86aa39d5286019d8519bff76e7eaea2dd808b47af4f6',
 'src/kicad_route/router/py_router/bga_fanout/layer_assignment.py': '0bd0b19c70e005c4af50df1fb9ecd9c31e3f3d00687d8336227fdc2d03baebb5',
 'src/kicad_route/router/py_router/bga_fanout/layer_balance.py': '7cf7cd7199af811d88bdd8e0f4c5bfb42dd31b17f92926876f2deb696542a59d',
 'src/kicad_route/router/py_router/bga_fanout/reroute.py': '34147e95f561b9af5d773e1fd035694499520edcb1b8e1a2d4297a2c474e12fc',
 'src/kicad_route/router/py_router/bga_fanout/rotate_frame.py': '40d2e0982ab823b550049077795ec18eec7798da97b860e1d382f8d7cec724e0',
 'src/kicad_route/router/py_router/bga_fanout/tracks.py': '7f02d835c8e7769c4e24d10775f402d2e8a3a3659a2b2ebd62101582bcd4a0d3',
 'src/kicad_route/router/py_router/bga_fanout/types.py': '812f7cecd8899804b2dad7a97c9ae33e8ac010120faa64da3ffad8a408ed0338',
 'src/kicad_route/router/py_router/bga_fanout/underpad.py': 'a9d98952a04726aebfdb6a561610ec735ee34df33512aa412100c4dce984d540',
 'src/kicad_route/router/py_router/blocker_resources.py': 'b3846b9a197bf980e8afc10c6ae2e248efd426c941cc3df7631284d239459b3d',
 'src/kicad_route/router/py_router/blocking_analysis.py': '654d626e140a29759dfdd061578c918c127319795aeba798bdce98c661044a2c',
 'src/kicad_route/router/py_router/bresenham_utils.py': '0da8a0d9d58a36aa6d5137ba7109ba19ff9dc6fd2e269d154d00663399c9dfd7',
 'src/kicad_route/router/py_router/bus_corridor.py': '951f22a3e22b4304f05335e350ff4af8173202a887ffdd0aff49f98e2613333a',
 'src/kicad_route/router/py_router/bus_detection.py': 'ada6f88d665c00ee4e6935e12f84810e37a1710e6e35194ede205220ba2f069d',
 'src/kicad_route/router/py_router/check_connected.py': '66997ad37e026b871dbdb2d3515d372f7ead4b13ed748abf9773cb63853961c3',
 'src/kicad_route/router/py_router/check_drc.py': '9abefbb16151d51bdbbc3f2c86000705ee173b19f6f235aa1f58251dfea33477',
 'src/kicad_route/router/py_router/check_pads.py': '6627b37eca7a7afb234b4ae2fc047e65d452a6da0ea647c05696af6b052cd59c',
 'src/kicad_route/router/py_router/check_weird.py': 'c5540de9647f7294da3d386e4d3f67cb3127bb9b0d07c43edfb0e83e547fcea3',
 'src/kicad_route/router/py_router/chip_boundary.py': '76d3867bca3340a2f44d466df7169e6a13d159d603d386256102c784649294b9',
 'src/kicad_route/router/py_router/cleanup_pipeline.py': '7c9c10a8c90f365c32360f1ce080ef32231d46cb9ac18d99f33f3ad04da4d1f4',
 'src/kicad_route/router/py_router/clearance_ledger.py': 'caa7c20b4347bf79c89aae6e21a33c973ab15fd6f6e4aa4879bccecf4f4e4c1b',
 'src/kicad_route/router/py_router/cli_banner.py': 'dfbce4fba8eb386ff0de8148b139c9fce4b8b9a2a904fb3c905af05b5734111f',
 'src/kicad_route/router/py_router/cli_nets.py': '773d1250d9316628f9d719fe695f76816d7c00ed416232c766cf6672de6d535e',
 'src/kicad_route/router/py_router/cmd_timing.py': 'bcc6834cd496c0765f074539fdc98b63ad2cd97f067652ce5bb0c5d4bc8b92f7',
 'src/kicad_route/router/py_router/congestion_field.py': '9ba4c0588df393403664ceaa452be6447e9dabc5d1a9e9a8fa0860a73ed6e1ad',
 'src/kicad_route/router/py_router/connectivity.py': '5384ff5ccc607a5b0fa0cca62a093295a45e9ba32411e37a3e8f42fde4ec06d1',
 'src/kicad_route/router/py_router/console_encoding.py': '788397094cc7175cfe2b595b14f1d20c2ab677d3b9e3a824a030889dd45479a9',
 'src/kicad_route/router/py_router/copper_motion.py': '6ce75c48d9344e5af739c01773b0c3a5f523e568d9486aa47d2cf510d5fb6772',
 'src/kicad_route/router/py_router/copy_board.py': '79cf1319ca961f10caa4d540afb0f3460d94dd8532214e0307ee89d1712e7877',
 'src/kicad_route/router/py_router/design_rules.py': 'e3e9260afde28bd4d5e8ac42f489826c708be2b28a143ca254360c2e9b0031ff',
 'src/kicad_route/router/py_router/diff_pair_custody.py': 'f07e2cec713bfeda971b68fbbdba2a6630244f5e1e54bf01df3529257835b173',
 'src/kicad_route/router/py_router/diff_pair_loop.py': '636f07595a727b3e81ec882762094ea90b6bc86b80c5ebad4b017abac3494a4f',
 'src/kicad_route/router/py_router/diff_pair_multipoint.py': '22aae608463906465d714ca94ddf4b7aff565814ab7ff7590c6207ff00d905f7',
 'src/kicad_route/router/py_router/diff_pair_routing.py': 'aa355c8a1a10fbb672000a78dfa7974bb7ab95fc9c079eca716639586d6726a0',
 'src/kicad_route/router/py_router/diff_xnet.py': '204665ebc1494103a7e6960aa43bfd0eada9d17c70d31dc6cda8be63e5909f24',
 'src/kicad_route/router/py_router/dup_trap.py': 'f3a3cc3b91d7318b46a091d14586ce31469c3101ddcd61b325bbda90e63fa1ee',
 'src/kicad_route/router/py_router/effective_config_evidence.py': 'fe9254c32d895f936e97f03f34f9a6edbb16fcad086179f62325cf0eeeb1722e',
 'src/kicad_route/router/py_router/effective_config_fields.json': '9aefa28f407fdcf30ea56be8378ad4bb2a481a91c93f0fbb00cff147813356fb',
 'src/kicad_route/router/py_router/env_knobs.py': '7b9c9fc08f0bad191aba5a5c1e8e7c058e2457c9c83b43c8b090a27b52fbc382',
 'src/kicad_route/router/py_router/fab_notes.py': '0683295464406c4a6ac992b48a78da47322bfcd1f830607c85b4b06f281cda14',
 'src/kicad_route/router/py_router/fab_tiers.py': '66e7156a4ee1507c575dd9b11c75c1d1c62b7cc1a518e8259965c888f0a8b39b',
 'src/kicad_route/router/py_router/film_passes.py': '857a08f1a1b72ad77e7aab9fa17bd9e9f7fd52b9755f47ace047bd9c54e36115',
 'src/kicad_route/router/py_router/fix_kicad_drc_settings.py': '34c67467b45297380b000e2be1cdb3b5ca857b0ef3d2c1a078295e79fffdb296',
 'src/kicad_route/router/py_router/frame_layout.py': 'b48541f17dc702bde45417cf1f807a738b47b6f66946d889ba55f6aa2fd11f87',
 'src/kicad_route/router/py_router/frame_spool.py': '41d4097012d9bd9b923a4fb7978203a56db476aa4a334cb0caa702a83e37bf60',
 'src/kicad_route/router/py_router/geometry_utils.py': 'ea599cc0b449b824dfc0ca692dcef9b1035aa56b060404cc29a57e0dbb9a852b',
 'src/kicad_route/router/py_router/global_plan.py': '56a9ad335c828aab0ffe4309b3f2763247b9a241ccbc9ab1e99affabdd2bb527',
 'src/kicad_route/router/py_router/group_routing.py': 'c5b2b08b0996a2704b1f9b91780838fc4c9b13d37291ada03de981fd67dfe491',
 'src/kicad_route/router/py_router/history_congestion.py': '1519450ca76dfad53467838a271d5c6fa8b099509f4413a6fbbe5cd7824f810e',
 'src/kicad_route/router/py_router/impedance.py': '1e3be5974d48207433d38b24baea0dbae755923de0eb8128ae1d92b36253368b',
 'src/kicad_route/router/py_router/improvement_gate.py': '9336728d5ad4df580207d1dee4af83b01c2dd1b0b59ca448fb824613671252d3',
 'src/kicad_route/router/py_router/keep_away.py': '83e28ea8b3904cfadc80b26e86b5866ae9d6bd800645fb8ac2231e312fbdf1e3',
 'src/kicad_route/router/py_router/kicad_dru.py': '0dc0a5ab50b9d0ca83b178071b1f56eb86cd6906160c4e90f0864ec6a9af4c96',
 'src/kicad_route/router/py_router/kicad_exact_fill.py': '12edfc03be0955dd95ccd20f6ddc5bcd435d72bbb13352151397f0ff558e7cef',
 'src/kicad_route/router/py_router/kicad_iso_render.py': 'cbbdb7ad575742ede223b5f1a2fd508210fe6486a076718170ff5a3f4417235c',
 'src/kicad_route/router/py_router/kicad_locate.py': '0fd5de7ea5024691f3e75947b1d70ea80048e77ee8c24cd78ebd3d46befb0f26',
 'src/kicad_route/router/py_router/kicad_oracle.py': '35c96fd9d9656b7a27ff0f04bb7758161bbc97f44a53e7a27802f03f47c64663',
 'src/kicad_route/router/py_router/kicad_parser.py': 'ffb7274af0b09fbacc8a070de86c50c3cb41c5aae0afd07702ac8a5e16e40370',
 'src/kicad_route/router/py_router/kicad_writer.py': '9cf41aa5fcc7f5332938e733933b4f3688dc71ed9c7dd73fda2c8d7c8c372718',
 'src/kicad_route/router/py_router/layer_swap_fallback.py': '3aefa517c4d401679b40c1b100a11f4721b6d78828699a11bdd9a2eb0bfd5ece',
 'src/kicad_route/router/py_router/layer_swap_optimization.py': '119b61cba4c0f2d0f1bb5421514ac1bedb0ba6dd4c7b58c28641871ab6c7ab71',
 'src/kicad_route/router/py_router/ledger_score.py': 'ce31d6080f0eceb3dc134ba0010f620ad1799cb01f8db93726e99d7fc85a881d',
 'src/kicad_route/router/py_router/leg_rip.py': '24c3fc9b503f2c7e0c2c0ef43e0c6b489b40e825f2d0abd69546f9f104c6022c',
 'src/kicad_route/router/py_router/length_matching.py': '4ece75938d0bd922e6de5a324be98115198e13b1dbf8a3f3af685c77a8270a87',
 'src/kicad_route/router/py_router/list_nets.py': '0faa09212907b347e48ce721d3c874125758ddf40d4f7bf48761072d7512908f',
 'src/kicad_route/router/py_router/make_movie.py': '670c89f82703cbe8bc401e9e13693b102cbcf756d3a746274ddab4111031b98b',
 'src/kicad_route/router/py_router/memory_debug.py': '6c0e549c44847f57ccfbfeb36f71f2e7b04fade388e0c12d5256971835243d63',
 'src/kicad_route/router/py_router/movie_attempts.py': '7bb978d7576314766fa33fd4e3aa26082303f17ced3bd03fb08cd86c4e784e1f',
 'src/kicad_route/router/py_router/movie_benchmark.py': 'd23010db2d435df793a24238766184db6a620706f22f4027795b76bac65c91e2',
 'src/kicad_route/router/py_router/movie_camera.py': '2fafc2e7f1eda4f603b7109531e9771cb509437340d6c3dcc82686f973c73052',
 'src/kicad_route/router/py_router/movie_placement.py': '12c22f1668add5daa8a1df911f25e0463823430d5faae65f24f45beb58deacf2',
 'src/kicad_route/router/py_router/mps_layer_swap.py': 'e744f54c365b8260a79885bb8faf24965384e0719c637885bba48b645bf3b432',
 'src/kicad_route/router/py_router/net_cost.py': '5d4964c0e296fbab73182fed2b091d6005080062d5a663ebb85f90b9e2ad9c66',
 'src/kicad_route/router/py_router/net_ordering.py': 'd6dab7157f9a42bfcee9f5c88505712b3da1f24bde4ac22269d1646cf2a08c87',
 'src/kicad_route/router/py_router/net_queries.py': '6b47c762cfe67ca0b0f432a4b47f149d52f82a10c2f5d8a1781d26b652275cd9',
 'src/kicad_route/router/py_router/net_rescue.py': '05345a1e780b44b18461f5040ce16ae8d8834f970441086cd8df3f9000a92f51',
 'src/kicad_route/router/py_router/net_story.py': 'a54ca5aff45e80d8a27b8b15fa4f4576a2bda803a8ce2ab4b7613e94101674ac',
 'src/kicad_route/router/py_router/obstacle_cache.py': 'bb14bb37aa30d951b8d5b642119b43fe521933c2b33aa5f0fef86be1a66e42eb',
 'src/kicad_route/router/py_router/obstacle_costs.py': '66afaf065eeafe21af70b361cc2f0cf64c9a6efb7da1b418b0379167093358ec',
 'src/kicad_route/router/py_router/obstacle_map.py': '24001dcd5ff72eeee3e485b38f429669f7382ad5193f9435d181df37f6d45fd9',
 'src/kicad_route/router/py_router/output_writer.py': 'c67c2267390b07fe0513e66561643d046ae92dfffc4234b7bb488f8ed504cd07',
 'src/kicad_route/router/py_router/pack_river.py': '34d54fb0f9ae956ab0507fa818cf604fdc8816a0b54508f23db9c742e7c31dc0',
 'src/kicad_route/router/py_router/paste_apertures.py': '0c7a864b934ae7ec812f0fb091d16385f442b8472fa34885b85f20219e7e6031',
 'src/kicad_route/router/py_router/pcb_io_utils.py': 'a128f2dc42e869dc46ac240d739e6523eb31a75ffec85798c396784ab7606892',
 'src/kicad_route/router/py_router/pcb_modification.py': '3c8da17cd58610d263a77a8703ff09df27974ff86d19e9162876089c00212a20',
 'src/kicad_route/router/py_router/phase3_routing.py': 'a98915bd729750687ff251a07118e04e510bd4366fa3ac8700cf10455c4ed8f3',
 'src/kicad_route/router/py_router/place_motion.py': '9fa0574956f290e947a1c842f0d6ffe38a7e8ff42b881ecf7b94780e78d00f09',
 'src/kicad_route/router/py_router/plane_blocker_detection.py': 'c439b516ff466034902153e29a52969d16002891506ada7d6b61d5f7ca1931d1',
 'src/kicad_route/router/py_router/plane_component_oracle.py': '845aee20a9d3080b1047f48023bb93fb9cebfb072962bca64e6a0a2f208ebf96',
 'src/kicad_route/router/py_router/plane_corridor_ghosts.py': 'dd2e5cc64a7946af47cdf98b6da9a5ac530eff2a64619c4774c2c81ce208a2e1',
 'src/kicad_route/router/py_router/plane_fill_model.py': '001778d66ca3b4b01309c3bb979fcba6a989d07495dcad1c7662b74fee00777a',
 'src/kicad_route/router/py_router/plane_fragility.py': 'c5d4fe82872c8767abb07c2a439dbf76701b0b426402469e2b7e01d4b081cd60',
 'src/kicad_route/router/py_router/plane_io.py': '4bd72c508a5be0eef831f1788c6e655b001bcc24b533341829cbc6e9cd4ca5e3',
 'src/kicad_route/router/py_router/plane_lattice.py': 'c96af256f7f1827cf7b5d2a1ec472eda1a841f61bfa817307cb762765015c353',
 'src/kicad_route/router/py_router/plane_obstacle_builder.py': '687a46abb8424777b89aaf6e36b9fca806698f829f43a601a4b570fab3a02805',
 'src/kicad_route/router/py_router/plane_pad_tap.py': '53fd384f7def41f3461c41cfcb53e09f48c25f4ceee791e8f9443b0b88ddf04f',
 'src/kicad_route/router/py_router/plane_region_connector.py': 'ef9aab2d3fa40191fa7e0978348ccfeab8981d96e2c47744921f8aedc1c73b34',
 'src/kicad_route/router/py_router/plane_resistance.py': '13ac213ca844508852f397853234815a3b5d5675afcad593206933130322005a',
 'src/kicad_route/router/py_router/plane_split_raster.py': 'c0e938278fde7ef7b38c31cfc8ed3d4bef6ba0e377941692b3088a4fe4f2a59f',
 'src/kicad_route/router/py_router/plane_write_reconcile.py': '1c257a0b76f329e040c3cf99f6499e283fccaa2ba4526217fe164ca2a114c9e3',
 'src/kicad_route/router/py_router/plane_zone_geometry.py': '8c2ba668b5a2bc40755b8701075296f3b7635813a260bc52af37282e1edaa095',
 'src/kicad_route/router/py_router/polarity_swap.py': '586014b0c93f79222eaaf2a6f0554a4edb379e615441bb26cb6e78351776d4b0',
 'src/kicad_route/router/py_router/pour_guard.py': '60890ff3814d6c49b01f37ed24bbab3457f896b50471578e6e6b58bc2e5192f3',
 'src/kicad_route/router/py_router/pour_promise.py': '55d2db38af8da4acf7298da184fb08868623b62474bcd77f28ec52b220b978f9',
 'src/kicad_route/router/py_router/protected_nets.py': '8e1aaa2002befb452e75948fd19a2ed4d1a9440a6b659b3dfce8be3f58174fd9',
 'src/kicad_route/router/py_router/redo_record.py': 'f167110134b8b5e385a67863bd22a02256b2b85c67566a9100b9859c19a7b143',
 'src/kicad_route/router/py_router/render_chrome.py': '1182ac85522eac27cfb1c3eb833df5299f039722ab612a55bd705f0580823f7c',
 'src/kicad_route/router/py_router/render_panels.py': '44c6e4e391e111b67395f636495c951a33dccb28e14891f19aa9ced79d06b780',
 'src/kicad_route/router/py_router/render_plan.py': '8972d1277db570ac91e33022d4ee8488f8b0c066f964ea1ccd6c42ee6f0b0dff',
 'src/kicad_route/router/py_router/render_theme.py': 'cb661cb989717f1c0ce6d0f1afad2485763527014b7cfd2364e7661f70b9ddf3',
 'src/kicad_route/router/py_router/repair_planes.py': 'e75fa4921ed5b1c518c4215117105cbcf0e262d0dff9d04f5406b1937f395c69',
 'src/kicad_route/router/py_router/reroute_loop.py': '7b5002ba9c5b412784372f3cd8c789bdec370180ba17c370506def89581bf2cd',
 'src/kicad_route/router/py_router/rip_defer.py': 'c965f8b2cdf65d10254735049b5eebafe72c4229d9d9efbedd10bd0dd06dfdb6',
 'src/kicad_route/router/py_router/rip_restore.py': '9a5a68cf13050035de319d8158807cea3104b79241ef15523a729edf42ae544b',
 'src/kicad_route/router/py_router/rip_up_reroute.py': 'e438891f67059b0376eae91c4bff82bb26f2dec9e702aee454927abc29cc5149',
 'src/kicad_route/router/py_router/route.py': '73fcddf34b99667dc0df4759457968e614bbd8047c50077a1a6b5ac65ab58c3c',
 'src/kicad_route/router/py_router/route_diff.py': '7eda4eea111d2a9b17706fa90bec282eacc2bc8572cc2e9f720f3a0804f3729a',
 'src/kicad_route/router/py_router/route_planes.py': '05cde89b474e1a48a2746dfdee7d819f2796009f9f6a2a55a602f32e6b940fcf',
 'src/kicad_route/router/py_router/route_render.py': '5fca0a39fbca4d58e0a77f503867e0fb48bed766ede31b9664a5a0b6c9f7131f',
 'src/kicad_route/router/py_router/route_summary.py': '3c8575595d90ca286dc06e36df2905aea583a10e986d13633fd36b5e8d026410',
 'src/kicad_route/router/py_router/route_trace.py': '4511cbacf524a9b72739ed06e4d1b1ff28cf035fef5aa634939cfba3f0c73e3b',
 'src/kicad_route/router/py_router/routing_common.py': 'a698bbb0c09d6be2bb9e87e741e4cb23bb563e0fe9a7ebe6ed29356a00166311',
 'src/kicad_route/router/py_router/routing_config.py': '001b54db7e0d92296add49a18665b625dfcae1ebc9951796de26037041a75a8f',
 'src/kicad_route/router/py_router/routing_constants.py': '5bd4f2aa4d42b5b8f581111bd2ef534a5a1aa53af81b7757fbecfff529c2f718',
 'src/kicad_route/router/py_router/routing_context.py': '2285fa2acee5d0cd1893dd0d95f100b54abd35a7fc625849ab69b4d050911f0e',
 'src/kicad_route/router/py_router/routing_defaults.py': '814cb2693c8029539b9a37726fa84c8b93ed76edbd32da31ac6722a80e75ddf9',
 'src/kicad_route/router/py_router/routing_diagnostics.py': '82aef147c608c83d897b8713073893b722bb71d0880f8575ac1c3d94afc46b15',
 'src/kicad_route/router/py_router/routing_exceptions.py': '2f881ffd0ea1eb1bb19d49c300d1ddac5a2da87a20403f62cc6026c9a5f4c03a',
 'src/kicad_route/router/py_router/routing_state.py': '38b8ce2090aa5029bf63ef657b3ef62ce66f03a553f193f570b442dd60f44c01',
 'src/kicad_route/router/py_router/routing_utils.py': 'a7d25f7423f4fcddc5be3ebbfedc31f3c70ae895963c23740679489ebd75e536',
 'src/kicad_route/router/py_router/rust_alloc.py': '838d142cd07a85f8f78b49b8a237f783525c1a548f545195aaa3008331a7655e',
 'src/kicad_route/router/py_router/schematic_updater.py': '3dc6cccbadd5e61ee99b08c11557566cb030d6cadf9916352357df6bf52582d8',
 'src/kicad_route/router/py_router/sexpr.py': '35568aef68b724b4e256cc427b9dd7f522d4297d399e3c4c918b212ea4a2d84b',
 'src/kicad_route/router/py_router/single_ended_loop.py': '11b1ab861d0d9a1ca6327a4bf97e813a507ec95e366bc59449d919f11b9d4d40',
 'src/kicad_route/router/py_router/single_ended_routing.py': '22a82aa1bc987c360a5faabde144827079981a7668fa997adbf0f8216286dc38',
 'src/kicad_route/router/py_router/stage3d/__init__.py': '161f136b3da4952b101ecd563d9bd3037acfa100d74339683eb0e197bd888970',
 'src/kicad_route/router/py_router/stage3d/blender_scene.py': 'd99c8de1bbccb507d5f4088fcf9eb99df42034c1632efc76b4c78aa6bbf551a2',
 'src/kicad_route/router/py_router/stage3d/film.py': 'fe6e00044a30326121b7d5cfb7fe7035f108eb9409207ee8a5b46ca5eb6cff6c',
 'src/kicad_route/router/py_router/stage3d/package-lock.json': '77fbbd720f5d2ecf2ebd226bceb0a3c8a64972d5ea5dda0809c4c8c18a40f442',
 'src/kicad_route/router/py_router/stage3d/package.json': '3c5c157fa742ef026c0307249d773387a23e361c76b38d30d5ea36990dc91df9',
 'src/kicad_route/router/py_router/stage3d/page/index.html': '40ced37a7e8fa68946c9959c20c399872aa9cc658052233a8896e4cc23745875',
 'src/kicad_route/router/py_router/stage3d/page/stage3d.mjs': '5ffa585a3c80ee727e9043c070511fc1a4fcdaeec1285bb22d33a4b02d1b3c74',
 'src/kicad_route/router/py_router/stage3d/render.mjs': '81dffb15754ebe01a63fda9801783d16e5c56ec62b15b8599840e63bcfa36401',
 'src/kicad_route/router/py_router/stage3d/render3d.py': '098cac31929a6d27291047f8c444da9a4ecb672748899fa64de1d69fbd5d6d17',
 'src/kicad_route/router/py_router/stage3d/scene.py': 'a99eadf1f6eb827858ce5135f88e5e1ced8fb1e223ea952f28ceeb86a716ec31',
 'src/kicad_route/router/py_router/stage3d/timeline.py': '85a8fa201161bf3f2e606dc8cc70afc3936f6988d1d1b505acf5427f6100f590',
 'src/kicad_route/router/py_router/stage3d/vendor/three/LICENSE': '8b378ebe60e2fe500158cb0ac71cb5e8b7d92953c2abcc63a0eb90499653b5bc',
 'src/kicad_route/router/py_router/stage3d/vendor/three/VERSION.txt': '1f4ccdfddd489858e33b31607735544ec7335960fc0f6d03b5cb0f7f61600519',
 'src/kicad_route/router/py_router/stage3d/vendor/three/build/three.core.js': '9edde002b066a9a05676a6127f67735b62baf399bdea529f2f7e31657da769e6',
 'src/kicad_route/router/py_router/stage3d/vendor/three/build/three.module.js': '9052042d676cb0fdc1ddfefe193053f34b7ac0513a616fdac4535d49987812ea',
 'src/kicad_route/router/py_router/stage3d/vendor/three/examples/jsm/loaders/GLTFLoader.js': '131c0f78c01d19368ae495caa65b3adaa10487810a36a05bb5901b769a35ac16',
 'src/kicad_route/router/py_router/stage3d/vendor/three/examples/jsm/utils/BufferGeometryUtils.js': '9fb63427ce6641fa14fd0baff9cc4d1b5f9c3d85fd084bf2e90e803c44ec1797',
 'src/kicad_route/router/py_router/stage3d/vendor/three/examples/jsm/utils/SkeletonUtils.js': 'b1632a703206c3d830de9fcbe515696770d04b71a15ee6b50afa6d2c3298c86f',
 'src/kicad_route/router/py_router/startup_checks.py': '2721cee806e90d48e9d1086a86876b615d2205c40b35420d6ee000fbfcb76bd3',
 'src/kicad_route/router/py_router/stub_layer_switching.py': 'cd4d5871d6c7af15dc111f73f873c66d2e22e9f168abc76275780d791d04d5ff',
 'src/kicad_route/router/py_router/swig_compat.py': '6c3d48c24a439231901b84d08467d8c59817a8993730a499cbcdfe071d46925f',
 'src/kicad_route/router/py_router/tap_relocation.py': 'c516170f8f4c6b7b5ebb880722032386baa4a2b852bd2d5cabb7ada225d990f1',
 'src/kicad_route/router/py_router/target_swap.py': '01bc14831d34493d1b7ea5903815a79ab6e8b55e62170b8fd52b8f3b48c6e00b',
 'src/kicad_route/router/py_router/terminal_colors.py': '824dbf2811eb5ab677a4440bbc44e8734445d628847fbf6b43e7da367761ae24',
 'src/kicad_route/router/py_router/tool_records.py': 'd459728ad947d6d078c3e0ec045425793f2832705e1a5e560942e2352ccda74c',
 'src/kicad_route/router/py_router/ui_thread.py': '434574ac58739a0370d469b1a387fc67b43e675b3b4d54dbb67485f4442b0070',
 'src/kicad_route/router/py_tools/_path.py': 'd58191abe45e6d016915dcbc7de0b357489237082370af35be654984a5e0ed46',
 'src/kicad_route/router/py_tools/board_brief.py': '22948dba3d97398885ed49545db59f452f488ac144a85ca7d990c6acc42053ff',
 'src/kicad_route/router/py_tools/board_context.py': 'c5160fac7c731659abe4c688a8ff5497a7511d192390d9ab0237b5908f4faa79',
 'src/kicad_route/router/py_tools/board_score.py': 'cba1191684ee564af0427f3479ef79d4289e0bbb8ecf6724b9f26f3e81c548e6',
 'src/kicad_route/router/py_tools/check_floorplan.py': '4b1751d6654a5b758961e828f1ec78b02fe13b11debd3c918e00147e976ed0b8',
 'src/kicad_route/router/py_tools/check_impedance.py': 'ea58a0fe25257c4a72c4a6171c7d8ae77614adc436e3ce1a709b736cb895e864',
 'src/kicad_route/router/py_tools/check_orphan_stubs.py': '21e7dab33cd719b96fcbb9190352bd5b95f20c459cb32573b9fe877a5d60ded3',
 'src/kicad_route/router/py_tools/check_reachability.py': '19a0e4f7ee73cbead1904593e58882cf7ab37afe6b179737629442f35c3ff3e2',
 'src/kicad_route/router/py_tools/render_placement.py': '24e6d63a04f8e5d2b3ad39db28504968c467d80c22bc0074c9e4583f1c4fd543',
 'src/kicad_route/router/requirements.txt': '139aba6f659752d19d8d7c467866bc78a6ced432b7d14b3e45bbfe24a7753e43',
 'src/kicad_route/router/rust_router/Cargo.lock': 'f14e7b0c06ae682e63f1b452862a72417cb69ee629f51ac49bac242a24d94203',
 'src/kicad_route/router/rust_router/Cargo.toml': 'b75f1a594d1933a7b539f123e356014f6af8c621864e01f6f0746d31330acf89',
 'src/kicad_route/router/rust_router/src/dubins.rs': '578db6b9034dcea6a90849323843e47792b3c0921427d2951d325bfdb6147730',
 'src/kicad_route/router/rust_router/src/lane_search.rs': '2e63ecb7c53943ce6188725600400be5dd9e0625fd5c36137948fb3f290c1ce9',
 'src/kicad_route/router/rust_router/src/lib.rs': '3bb11f80ee27862414a32b9774f19d087749994d8f4a163af73546452120e44e',
 'src/kicad_route/router/rust_router/src/obstacle_map.rs': '87b163ec610b24ce3048573addcf902252a0463d0fdc1a0d3abb6df5258edd5e',
 'src/kicad_route/router/rust_router/src/pair_search.rs': 'f384c7331e677f6821176da6d36d3e51a00fd4bb9f06151e7520963b463c5204',
 'src/kicad_route/router/rust_router/src/pose_router.rs': 'a4df246ef8a886d1cd3df989734bfd4a15ddc72e551905b046fbbe056c240a4c',
 'src/kicad_route/router/rust_router/src/router.rs': '0b91bd986e9a5f09004d8e97a8d3978a34d6e42ceef3f9141fa5b1b517b13eb3',
 'src/kicad_route/router/rust_router/src/types.rs': '70058395d6c08ccefbe7b26596025adb60724767565182d8eca2db42ebccbfe4',
 'src/kicad_route/router/tests/native_lifecycle_support.py': '389281a829ad74f984a7639b65f6c622776e4de1e786dfc7028d64d79edbfce9',
 'src/kicad_route/router/tests/regression_utils.py': '04c419bc0c20318c9b4b851c74f7dc59a971f1b92471cb19a5d09f62450060dd',
 'src/kicad_route/router/tests/stub_swap_fixtures.py': '6b1ad57117f95143f0389049b314ff7073e13277a7983e07857cbd5dc46b786e',
 'src/kicad_route/router/tests/synth.py': '8a5e34c6c904eadb0ed90dfaba395f150ecfbc650511528ccf4ba80b6ba45177',
 'src/kicad_route/router/tests/test_blocker_resources.py': 'a50793b26e351c275cc27eb00e6f40e4412575ae26f902f0825f44ae1b473b7e',
 'src/kicad_route/router/tests/test_diff_keepout_member_clearance.py': '02e9827d719ee1eee7f136f613f4e8e5dafbc7566a7b1fb7046df28002b662ea',
 'src/kicad_route/router/tests/test_kernel.py': '0dee4f21f4f6b33fedbc0546bad952718ad7f29d4e1240dc91483dd79f4adade',
 'src/kicad_route/router/tests/test_krt64_tap_grid.py': '65e10213bd0429937972ed976d67667ab3b63b79714fa0ee170c547adeaf0c1c',
 'src/kicad_route/router/tests/test_no_stub_swap_reaches_config.py': 'b721f346a41811d4da00979e40093958f8eba12162bfb7a1d5f63906d41e9297',
 'src/kicad_route/router/tests/test_obstacle_addremove_parity.py': 'af04343976986e529a4d39eec6e2443788331d70c448061120b2ea0cee28c85d',
 'src/kicad_route/router/tests/test_obstacle_lifecycle.py': 'e15624c1914319b803bd3751a9ed6fdaabdf50c493a6ffec0d22e3f73d69f517',
 'src/kicad_route/router/tests/test_parser_cache.py': '1ba66204abcaa32a31242d058e3ff3fbabc362a9419b553cd25cc6cd8a4ab47e',
 'src/kicad_route/router/tests/test_parser_legacy_tokens.py': 'e7218781732cd73c1f65fd75d3f7dff6dfdbeba0a790f85638d608b9fa21a033',
 'src/kicad_route/router/tests/test_pass_rule_floors.py': '6407e91ca4cec60c45d8d2f730c931b9830c61e17cb3a2142b8a6aa5d636e902',
 'src/kicad_route/router/tests/test_pour_launch_forbidden_layer_welds.py': 'be2c95e968234c53a85c07c1fea626a868dbb28211cf4b88d91335c00e728a2b',
 'src/kicad_route/router/tests/test_rescue_custody.py': 'ff760f15c2e66b9998c91cce0bb7783bc4935286e78973a0054feec86081600d',
 'src/kicad_route/router/tests/test_routable_layers.py': 'ba0c95ddadd2411dcc051f0f83d99e93a5fbc394515b389e186e9270cf08660c',
 'src/kicad_route/router/tests/test_stub_swap_via_hole_to_hole.py': '8b9e1d29d762ada6e5b74480daf05f7c6f40ba4679481966920230200f9b2114',
 'src/kicad_route/router/tests/test_writer_quotes.py': '95822e7e080513f51aa2e9df1edd61d1fdbf536194bef84abc52bdb68c6f98c0',
 'src/kicad_route/router/tests/test_writer_removal_identity.py': 'aded55348599d148a698b502a14d029addcb7558be7223b41d517a6b36b17a25',
 'src/kicad_route/sandbox.py': '344bfb3313c48947bbff262a6fb6b7b0661712687663925901027fe877b182a3',
 'src/kicad_route/skills.py': '5bb0cf561e9869082e8d8f4f3a971778a912b491f54e9dfd4c348e814bb6f703',
 'src/kicad_route/snapshot.py': '7c51203f4a439cc99a575e6bc5433f3ab78dda7735dcd03c940f6169fe7fb9a4',
 'src/kicad_route/spokes.py': 'fc5b3f90c31cbcc9a9944628bfe6b9bc93dc31de21c893f8e476160f21033506',
 'src/kicad_route/stall.py': '095445837a1621ca8eaa62fee2f87ec772d13fcb9111e75528743f24f095d188',
 'src/kicad_route/statemanifest.py': '40c60f955e643b651128963cd9720c29e6dbbf4dcd25c68c12616fb0a1972322'}
RESULT_SCHEMA = 'kicad-route/route/1'

REPORT_SCHEMA = 'agent-ee.kicad-route-attempt/1'
# Every report has exactly these keys, whatever the outcome.
REPORT_KEYS = {'schema', 'adapter', 'request', 'reproducibility', 'tool_version', 'backend', 'process',
               'discovery', 'missing_artifacts', 'retained', 'omitted_design_files', 'candidate',
               'candidate_editor_state', 'candidate_files', 'rejected_candidate_sha256', 'inputs', 'tool',
               'state_write_probe', 'identity_before', 'identity_after', 'semantics', 'identity'}
TOOL_VERSION = {'status': 'unknown', 'value': None,
                'reason': 'kicad-route at the pin has no --version; identity is the pinned code'}
NO_BACKEND = {'status': 'unknown', 'value': None, 'reason': 'no result.json'}
SANDBOX = {'work': '/work', 'candidate': '/candidate', 'checkout': '/opt/kicad-route',
           'state': '/opt/kicad-route-state'}
# AGENTEE-442 (agent-507 origin adjudication; agent-449 seam decision): kicad-route
# experiment._origin reads a --dir whose parent is named 'candidate', or whose
# grandparent holds attempt.json or result.json, as a run's candidate with a missing
# predecessor, so the read-only /candidate/<project> mount is never an original. The
# mount stays; the router is given a real, byte-exact copy of the verified input tree,
# staged in routing/ under this neutral name (never in the router's --out), and
# report.inputs.staged records and binds it.
ORIGINAL_STAGE = 'agent-ee-original'
PYTHON = '/usr/bin/python3'
# KICADROUTE-82: 'builtin' is the canonical name of the owned backend and 'krt' its deprecated
# alias. agent-ee emits only 'builtin' (AGENTEE-450). 'krt' is read-only legacy: a request or
# retained record keeps the name it was written with (receipts that say 'krt' keep their
# bytes), and names are normalized only to compare or look up.
BACKEND_ALIASES = {'krt': 'builtin'}
BACKENDS = ('builtin', 'krt', 'freerouting')
# What the pinned cli.py is given for --backend: the canonical name, whatever the request spelled.
PIN_BACKEND_NAMES = {'builtin': 'builtin', 'krt': 'builtin', 'freerouting': 'freerouting'}


def canonical_backend(name):
    """The canonical spelling of a backend name; an unknown or non-string name is returned unchanged."""
    return BACKEND_ALIASES.get(name, name) if isinstance(name, str) else name


# Installed backend inputs the pinned cli.py reads under KICAD_ROUTE_STATE, in the
# legacy layout (no router generation selected; the image's install.sh builds this).
STATE_COMPONENTS = {'krt': ('.venv-krt',), 'builtin': ('.venv-krt',), 'freerouting': ('vendor/freerouting',)}
STATE_REQUIRED = {'krt': ('.venv-krt/bin/python',), 'builtin': ('.venv-krt/bin/python',),
                  'freerouting': ('vendor/freerouting',)}
# A router generation (`kicad-route update` at the pin) is one directory under the state
# that holds the copied engine source and its venv; the manifest's krt.generation selects
# it. These are the files the pin's common.router_generation requires before selecting it.
GENERATION = re.compile(r'\.router-generations/[0-9a-f]{32}\Z')
GENERATION_REQUIRED = ('installation.json', 'router/VERSION', 'router/UPSTREAM.json', 'venv/bin/python')


def state_generation(state):
    """The router generation the state manifest at `state` selects, or None (legacy layout).

    Returns (generation, problems). An absent or unreadable manifest selects nothing
    here; verify_state reports it. A selection the pin would not resolve, or one
    outside STATE/.router-generations, is a problem, never a fallback to .venv-krt."""
    try:
        manifest = json.loads((Path(state) / 'manifest.json').read_bytes())
    except (OSError, ValueError):
        return None, []
    krt = manifest.get('krt') if isinstance(manifest, dict) else None
    generation = krt.get('generation') if isinstance(krt, dict) else None
    if generation is None:
        return None, []
    if not isinstance(generation, str) or not GENERATION.match(generation):
        return None, [f'state manifest selects an invalid router generation {generation!r}']
    return generation, []


def state_components(backend, generation=None):
    """Installed state directories the backend reads: the selected generation, or the legacy ones."""
    if canonical_backend(backend) == 'builtin' and generation is not None:
        return (generation,)
    return STATE_COMPONENTS[backend]


def state_required(backend, generation=None):
    """Installed files the backend cannot start without, for the state's layout."""
    if canonical_backend(backend) == 'builtin' and generation is not None:
        return tuple(f'{generation}/{rel}' for rel in GENERATION_REQUIRED)
    return STATE_REQUIRED[backend]


def generation_of_components(backend, components):
    """Recover the layout a report's state identities were taken in; ValueError if none matches."""
    components = tuple(components)
    if components == STATE_COMPONENTS[backend]:
        return None
    if (canonical_backend(backend) == 'builtin' and len(components) == 1 and
            isinstance(components[0], str) and GENERATION.match(components[0])):
        return components[0]
    raise ValueError('state identities name neither the legacy nor a router-generation layout')


def runtime_paths(layout, generation):
    """backend.runtime as the pin reports it when it runs the engine verified in this layout."""
    checkout, state = Path(layout['checkout']), Path(layout['state'])
    if generation is None:
        return {'source': str(checkout / 'src/kicad_route/router'), 'venv': str(state / '.venv-krt'),
                'generation': None}
    return {'source': str(state / generation / 'router'), 'venv': str(state / generation / 'venv'),
            'generation': str(state / generation)}
SAFE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z')
STAMP = re.compile(r'\d{8}T\d{6}\.\d{6}Z\Z')
# Every top-level result.json/attempt.json key cli.run_attempt writes at PIN
# (protect=existing). Any other shape is an incompatible tool.
RESULT_KEYS = {'schema', 'measurement', 'evidence', 'tool_identity', 'sandbox', 'kind', 'backend',
               'input', 'settings', 'scope', 'unsupported', 'floor', 'process',
               'routing', 'validation', 'candidate', 'run_dir', 'runtime_s', 'tool_versions',
               'finished_utc', 'exit_code', 'ledger'}
# Added by the pin only for a builtin attempt whose process passed (KICADROUTE copper custody).
# It is provenance with replay_authority false; agent-ee binds its bytes and never grades from it.
COPPER_OWNERSHIP_RECORD = 'validate/copper-ownership.json'
COPPER_OWNERSHIP_BOUND = {'status', 'record', 'sha256', 'coverage', 'replay_authority'}
COPPER_OWNERSHIP_UNAVAILABLE = {'status', 'reasons', 'replay_authority'}
# Nested keys the pin adds (KICADROUTE generations and measurement qualification): every
# result.json's process carries measurement_qualification, and an owned-engine result's
# backend carries the runtime it executed, which must be the engine verified before the run.
MEASUREMENT_QUALIFICATION = {'measurement', 'build_reproducible', 'reasons'}
RUNTIME_KEYS = {'source', 'venv', 'generation'}
ATTEMPT_KEYS = {'schema', 'backend', 'project', 'board', 'schematic', 'input_fingerprint', 'source',
                'reference_dir', 'candidate_dir', 'reference_facts', 'baseline', 'scope',
                'settings', 'floor', 'rules_files'}
PROCESS = ('PASS', 'FAIL')
ROUTING = ('COMPLETE', 'INCOMPLETE', 'NOT_RUN')
VALIDATION = ('PASS', 'FAIL', 'NOT_RUN')
RETAINED_FILES = ('result.json', 'attempt.json', 'RESULT.md', 'snapshot.json')
# Reports an attempt that wrote a result is expected to have; absent ones are listed, never invented.
EXPECTED_REPORTS = ('result.json', 'attempt.json', 'RESULT.md', 'validate')
RETAINED_DIRS = ('validate', 'facts', 'work')
# Design files never enter the retained diagnostics: the only board that can
# leave the attempt is the validated candidate of an eligible exit.
DESIGN_SUFFIXES = ('.kicad_pcb', '.kicad_sch', '.kicad_pro', '.kicad_dru', '.kicad_prl',
                   '.kicad_sym', '.kicad_mod', '.kicad_wks')
JSON_LIMIT = 16 * 1024 * 1024
EXIT_REQUEST, EXIT_CONTRADICTION, EXIT_ADAPTER_ERROR, EXIT_TIMEOUT, EXIT_CANCELLED = 64, 65, 70, 75, 143
EXIT_POLICY = 5
# classification -> (proposal, routing coverage, adapter exit)
CLASSES = {
    'complete': ('eligible', 'routing_complete', 0),
    'incomplete': ('eligible_partial', 'routing_incomplete', 2),
    'backend_failure': ('none', 'missing_routing', 1),
    'validation_failed': ('none', 'missing_routing', 3),
    'refused': ('none', 'missing_routing', 4),
    'policy_failed': ('none', 'missing_routing', EXIT_POLICY),
    'contradiction': ('none', 'unknown', EXIT_CONTRADICTION),
    'timeout': ('none', 'unknown', EXIT_TIMEOUT),
    'cancelled': ('none', 'unknown', EXIT_CANCELLED),
    'not_run': ('none', 'unknown', EXIT_REQUEST),
}
REPRODUCIBILITY = {'krt': 'deterministic backend, repeatability not qualified by this attempt',
                   'builtin': 'deterministic backend, repeatability not qualified by this attempt',
                   'freerouting': 'attempt only; never a reproducible measurement'}


class RequestError(ValueError):
    pass


# Declared layer policy (AGENTEE-231): allowed KRT signal layers, optional KRT
# cost multipliers in the same order, kicad-route's pour area-loss bound and an
# optional reference plane whose fill must lie under the declared RF nets, and
# optional per-net allowed signal layers (AGENTEE-258), graded on the routed board.
COPPER_LAYER = re.compile(r'(F|B|In[1-9][0-9]?)\.Cu\Z')
NET_NAME = re.compile(r'[^\x00-\x1f\x7f]{1,256}\Z')
POLICY_KEYS = {'signal_layers', 'pour_area_loss_pct'}
POLICY_OPTIONAL = {'layer_costs', 'reference', 'per_net_signal_layers'}
REFERENCE_KEYS = {'net', 'layers', 'rf_nets'}
POLICY_FILE = 'agent-ee-reference-continuity.json'
# Uncovered area below this is boolean rounding, not a gap in the plane: the
# clearance KiCad's zone filler cut for the RF net's own pads is rebuilt with the
# filler's own geometry, which leaves about 2e-8 mm2 per pad crossing.
CONTINUITY_TOLERANCE_MM2 = 0.000001
# The zone filler's extra clearance beyond the rule clearance (KiCad's
# ExtraZoneClearance default): without it a clean pad launch leaves 0.00025 mm2.
FILLER_EXTRA_CLEARANCE_MM = 0.0005
# A board rules file with these constraints can change the zone-to-pad clearance,
# which pcbnew's Python cannot evaluate: continuity is then not measured.
CLEARANCE_RULE = re.compile(r'\(\s*constraint\s+(clearance|hole_clearance|physical_clearance|'
                            r'physical_hole_clearance)\b')
# Reference continuity is a 2D check: the declared plane's fill covers each RF
# track's footprint. It does not qualify impedance or the return path.
CONTINUITY_SCOPE = ('2D fill coverage of the declared reference plane under each RF track footprint; '
                    'not an impedance or return-path qualification')
# A pre-existing track on a disallowed layer is exempt only by this geometry,
# matched one to one against the input board's own facts.
TRACK_IDENTITY = ('kind', 'layer', 'net', 'width', 'start', 'mid', 'end')

# KICADROUTE-91 M1b experiment evidence (AGENTEE-442). result.json may carry
# experiment = {path, sha256, schema}; path is relative to the router's run
# directory and is always EXPERIMENT_SIDECAR. --experiment-policy PATH gives the
# router a routability-policy/1 declaration with exactly the five fields
# kicad_route.experiment_policy.load accepts (reviewed at kicad-route a5fa636d).
# The adapter retains the sidecar with its recomputed identity or refuses it with
# a typed reason; it is provenance, never qualified. The root sidecar and the
# router's retained policy copy EXPERIMENT_POLICY_COPY are retained even when
# result.json does not reference them, and are then refused as unbound, never
# dropped. The sidecar, the policy copy and the result.json binding are read with
# strict_json: a repeated key, a non-finite or out-of-range number or excessive
# nesting is malformed evidence, never a crash or a last value. The sidecar shape
# is the one kicad-route a0ed434 writes (schemas/routability-experiment-1.json,
# replayed from its real clean native receipt in tests/fixtures/k91-real): exactly
# EXPERIMENT_SIDECAR_KEYS and EXPERIMENT_IDENTITY_KEYS, identity_sha256 the
# digest of identity, replay_authority false, and artifacts binding the policy
# copy, which kicad-route writes as json.dumps(policy, indent=2) + newline. A
# receipt the producer marks not comparison_eligible is refused as 'ineligible'
# when the coordinator declared a policy (the experiment it asked for is not a
# valid one) and otherwise retained with that marking. A receipt that claims
# comparison_eligible is requalified by _requalify from the retained bytes and
# refused when that proof is missing or unsupported; the flag is never
# authority. The whole sidecar must fit EXPERIMENT_SHAPE. Nothing is pinned or
# qualified by this: EXPERIMENT_CONTRACT names the unpinned producer.
EXPERIMENT_SCHEMA = 'routability-experiment/1'
EXPERIMENT_POLICY_SCHEMA = 'routability-policy/1'
EXPERIMENT_CLASSES = ('current-policy', 'historical-all-layer')
EXPERIMENT_REFERENCE_KEYS = {'path', 'sha256', 'schema'}
EXPERIMENT_POLICY_KEYS = {'schema', 'experiment_class', 'allowed_trace_layers', 'allowed_via_spans',
                          'rule_areas_sha256'}
EXPERIMENT_SIDECAR_POLICY_KEYS = {'declared', 'declared_sha256', 'experiment_class'}
EXPERIMENT_SIDECAR_KEYS = {'schema', 'identity', 'identity_sha256', 'comparison_eligible', 'ineligible_reasons',
                           'policy_qualification', 'native_terminals', 'candidate', 'artifacts', 'outcome',
                           'replay_authority'}
EXPERIMENT_IDENTITY_KEYS = {'input', 'origin', 'scope', 'protection', 'request', 'policy', 'budgets', 'native',
                            'build', 'effective'}
EXPERIMENT_JSON_DEPTH = 64
# The effective-configuration manifest and request an eligible sidecar must seal
# (kicad-route experiment.CONFIGURATION and its request), the one coverage and
# execution this boundary can requalify, and the manifest fields the sealed
# identity.effective restates verbatim.
EXPERIMENT_MANIFESTS = ('work/effective-configuration.json', 'work/effective-configuration-request.json')
EXPERIMENT_MANIFEST_SCHEMA = 'kicad-route/effective-configuration/1'
EXPERIMENT_REQUEST_SCHEMA = 'kicad-route/effective-configuration-request/1'
EXPERIMENT_COVERAGE = 'resolved_outer_batch_only'
EXPERIMENT_EXECUTION = {'outer_batches': 1, 'later_subruns': 0}
EXPERIMENT_RESTATED = ('schema', 'capture_status', 'coverage', 'stage', 'execution', 'not_captured',
                       'configuration', 'effective_environment', 'runtime_policy', 'scope', 'input')
# The three board seals of an eligible receipt (kicad-route a0ed434), each bound to its
# own stage record: the raw backend output (the sealed design artifact the adapter
# observes but never retains, the manifest output and the result.json binding), the
# wrapper output before native refill (the board kicad-route delivered to validation)
# and the graded, refilled board (validated, candidate and file digests).
EXPERIMENT_RAW_OUTPUT = 'work/effective-configuration-output.kicad_pcb'
EXPERIMENT_RAW_STAGE = 'backend_output_before_wrapper_lock_restore_and_native_refill'
EXPERIMENT_WRAPPER_STAGE = 'after_wrapper_postprocessing_before_native_refill'
# The reports an eligible sidecar must name by digest, and the attempt settings its
# identity.request restates (kicad-route records both from the same run options).
EXPERIMENT_BOUND = ('attempt.json', 'facts/reference-facts.json', 'validate/candidate-facts.json',
                    *EXPERIMENT_MANIFESTS, EXPERIMENT_RAW_OUTPUT)
EXPERIMENT_REQUEST_SETTINGS = ('protect', 'mode', 'max_iterations', 'passes', 'default_clearance_mm',
                               'clearance_margin_mm', 'pour_area_loss_pct', 'pour_min_spokes', 'backend_args',
                               'backend_env', 'reserve_spokes', 'barrier', 'rule_floor_sha256')
_UNSTATED = object()
EXPERIMENT_POLICY_FILE = 'agent-ee-experiment-policy.json'
EXPERIMENT_SIDECAR = 'routability-experiment.json'
EXPERIMENT_POLICY_COPY = 'experiment-policy.json'
EXPERIMENT_CONTRACT = ('agent-ee/AGENTEE-442: routability-experiment/1 as written by kicad-route '
                       'a0ed43494d44fd8312054b204083881fe76f3a81 (unpinned, not qualified); '
                       'routability-policy/1 five fields')
EXPERIMENT_REFUSALS = ('missing', 'malformed', 'unknown_schema', 'tampered', 'incompatible_schema',
                       'incompatible_policy', 'unbound', 'ineligible', 'unqualified', 'run_mismatch')
SHA256 = re.compile(r'[0-9a-f]{64}\Z')
# The path type retain() records, in place of a digest, for a prepared design-file
# name under work/input that is not a regular file (agent-510 R4).
PATH_TYPES = {'link': 'link', 'dir': 'special', 'other': 'special'}


def _shape(kind, **rules):
    """One node of EXPERIMENT_SHAPE: JSON type name(s) plus the JSON-schema rules it uses."""
    return {'type': (kind,) if isinstance(kind, str) else tuple(kind), **rules}


def _closed(required, optional=None, nullable=False):
    """An object with exactly the required keys plus any optional ones (additionalProperties false)."""
    return _shape(('object', 'null') if nullable else 'object', properties={**required, **(optional or {})},
                  required=tuple(required), closed=True)


# kicad-route a0ed434 schemas/routability-experiment-1.json, restated for the
# stdlib validator _shape_error: every required member, its JSON type, each
# const/enum, every sha256 and sha256-or-null leaf, and each object the producer
# schema closes. A sidecar outside it is malformed evidence (review H2, ee442e).
_SHA = _shape('string', pattern=SHA256)
_SHA_OR_NULL = _shape(('string', 'null'), pattern=SHA256)
_STR_OR_NULL = _shape(('string', 'null'))
_OBJ_OR_NULL = _shape(('object', 'null'))
_STRINGS = _shape('array', items=_shape('string'))
_NAMED_FILE = _closed({'name': _STR_OR_NULL, 'sha256': _SHA_OR_NULL},
                      {'not_loaded': _shape(('array', 'null'), items=_shape('string'))})
_EFFECTIVE = _closed({
    'schema': _shape('string', const='kicad-route/effective-configuration/1'),
    'capture_status': _shape('string', const='complete'), 'coverage': _shape('string'),
    'stage': _shape('string'), 'execution': _shape('object'), 'not_captured': _STRINGS,
    'configuration': _shape('object'), 'effective_environment': _shape('object'),
    'runtime_policy': _shape('object'), 'rules_sha256': _SHA_OR_NULL,
    'routing_environment': _shape('object', values=_shape('string')), 'invocation': _STRINGS,
    'scope': _STRINGS, 'net_inventory_sha256': _SHA_OR_NULL,
    'input': _closed({'board_sha256': _SHA_OR_NULL,
                      'rules': _closed({'.kicad_pro': _SHA_OR_NULL, '.kicad_dru': _SHA_OR_NULL})}, nullable=True)},
    nullable=True)
EXPERIMENT_SHAPE = _closed({
    'schema': _shape('string', const=EXPERIMENT_SCHEMA),
    'identity': _closed({
        'input': _closed({
            'fingerprint': _STR_OR_NULL, 'board': _NAMED_FILE, 'project': _NAMED_FILE, 'rules': _NAMED_FILE,
            'files': _shape(('object', 'null'), values=_SHA), 'snapshot_files': _shape(('object', 'null'), values=_SHA),
            'netlist_sha256': _SHA_OR_NULL, 'nets_sha256': _SHA_OR_NULL, 'placement_sha256': _SHA_OR_NULL,
            'mechanical_sha256': _SHA_OR_NULL, 'setup_sha256': _SHA_OR_NULL,
            'native_facts_kicad_version': _STR_OR_NULL, 'source': _OBJ_OR_NULL}),
        'origin': _closed({
            'root_input_fingerprint': _STR_OR_NULL, 'depth': _shape('integer', minimum=0),
            'predecessors': _shape('array', items=_closed({
                'attempt_sha256': _SHA_OR_NULL, 'candidate_board_sha256': _SHA_OR_NULL,
                'experiment_sha256': _SHA_OR_NULL}))}),
        'scope': _OBJ_OR_NULL,
        'protection': _shape('object', required=('mode', 'only_sha256')),
        'request': _shape('object'),
        'policy': _closed({
            'declared': _OBJ_OR_NULL, 'declared_sha256': _SHA_OR_NULL,
            'experiment_class': _shape(('string', 'null'), enum=(*EXPERIMENT_CLASSES, None)),
            'physical_stack': _shape(('array', 'null'), items=_shape('string')),
            'input_rule_areas_sha256': _SHA_OR_NULL, 'input_rule_areas_coverage': _shape('object')}),
        'budgets': _closed({'backend_s': _shape(('integer', 'null')), 'stall_s': _shape(('number', 'null')),
                            'command_budget_s': _shape(('integer', 'null')), 'grace_s': _shape(('number', 'null'))}),
        'native': _closed({'kicad_cli': _OBJ_OR_NULL, 'pcbnew_build': _STR_OR_NULL,
                           'terminal_producer': _OBJ_OR_NULL, 'terminal_native_identity': _OBJ_OR_NULL}),
        'build': _closed({'wrapper': _OBJ_OR_NULL, 'backend': _shape('object'), 'execution_build_id': _STR_OR_NULL,
                          'executed_source_sha256': _SHA_OR_NULL, 'producer_sha256': _SHA_OR_NULL,
                          'state_manifest_sha256': _SHA_OR_NULL}),
        'effective': _EFFECTIVE}),
    'identity_sha256': _SHA,
    'comparison_eligible': _shape('boolean'),
    'ineligible_reasons': _STRINGS,
    'policy_qualification': _shape('object', required=(
        'status', 'reasons', 'policy_sha256', 'rule_areas_sha256', 'coverage', 'actual_trace_layers',
        'actual_via_spans', 'effective_layers', 'effective_via'), properties={
        'status': _shape('string', enum=('PASS', 'FAIL', 'NOT_RUN')), 'reasons': _STRINGS,
        'actual_trace_layers': _STRINGS, 'actual_via_spans': _shape('array', items=_STRINGS)}),
    'native_terminals': _shape('object', values=_shape('object', required=(
        'board_sha256', 'inventory_sha256', 'terminal_nets', 'incomplete_nets', 'component_deficit'))),
    'candidate': _closed({
        'raw_backend_output_board_sha256': _SHA_OR_NULL, 'backend_output_board_sha256': _SHA_OR_NULL, 'pre_refill_board_sha256': _SHA_OR_NULL,
        'candidate_board_sha256': _SHA_OR_NULL, 'candidate_copper_signature': _OBJ_OR_NULL,
        'validated_board_sha256': _SHA_OR_NULL, 'file_sha256': _SHA_OR_NULL}),
    'artifacts': _shape('object', values=_SHA_OR_NULL),
    'outcome': _closed({'process': _STR_OR_NULL, 'routing': _STR_OR_NULL, 'validation': _STR_OR_NULL,
                        'unconnected_total': _shape(('integer', 'null')),
                        'backend_runtime_s': _shape(('number', 'null')), 'wall_clock_stop': _shape('boolean')}),
    'replay_authority': _shape('boolean', const=False)})
_JSON_TYPES = {'string': (str,), 'null': (type(None),), 'object': (dict,), 'array': (list,), 'boolean': (bool,),
               'integer': (int,), 'number': (int, float)}


def _shape_error(value, shape, where='sidecar'):
    """The first place value leaves shape, as 'where: what', or None. Iterative, so nesting
    is bounded by strict_json's EXPERIMENT_JSON_DEPTH rather than by recursion."""
    stack = [(value, shape, where)]
    while stack:
        value, shape, where = stack.pop()
        # bool is a Python int, never a JSON integer or number.
        kinds = [k for k in shape['type'] if isinstance(value, _JSON_TYPES[k]) and
                 not (isinstance(value, bool) and k in ('integer', 'number'))]
        if not kinds:
            return f'{where} must be {" or ".join(shape["type"])}'
        if 'const' in shape and canonical_json(value) != canonical_json(shape['const']):
            return f'{where} must be {canonical_json(shape["const"])}'
        if 'enum' in shape and canonical_json(value) not in map(canonical_json, shape['enum']):
            return f'{where} must be one of {canonical_json(list(shape["enum"]))}'
        if isinstance(value, str) and 'pattern' in shape and not shape['pattern'].match(value):
            return f'{where} must be 64 lowercase hex digits'
        if isinstance(value, int) and 'minimum' in shape and value < shape['minimum']:
            return f'{where} must be at least {shape["minimum"]}'
        if isinstance(value, dict):
            missing = [k for k in shape.get('required', ()) if k not in value]
            if missing:
                return f'{where} lacks {", ".join(missing)}'
            extra = sorted(set(value) - set(shape['properties'])) if shape.get('closed') else []
            if extra:
                return f'{where} has unsupported fields {", ".join(extra)}'
            for key in sorted(value, reverse=True):
                inner = shape.get('properties', {}).get(key, shape.get('values'))
                if inner is not None:
                    stack.append((value[key], inner, f'{where}.{key}'))
        if isinstance(value, list) and 'items' in shape:
            stack.extend((item, shape['items'], f'{where}[{i}]') for i, item in reversed(list(enumerate(value))))
    return None


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _number(value, low, high):
    return (type(value) in (int, float) and value == value and low <= value <= high)


def _names(value, pattern, label):
    if (not isinstance(value, list) or not value or len(value) != len(set(map(str, value))) or
            any(not isinstance(v, str) or not pattern.match(v) for v in value)):
        raise RequestError(f'{label} must be a nonempty list of distinct names matching {pattern.pattern}')
    return value


def validate_layer_policy(policy):
    """The declared layer policy, unchanged, or RequestError."""
    if (not isinstance(policy, dict) or not POLICY_KEYS <= set(policy) or
            not set(policy) <= POLICY_KEYS | POLICY_OPTIONAL):
        raise RequestError('layer policy must have signal_layers and pour_area_loss_pct, '
                           'optionally layer_costs, reference and per_net_signal_layers')
    _names(policy['signal_layers'], COPPER_LAYER, 'signal_layers')
    if 'per_net_signal_layers' in policy:
        nets = policy['per_net_signal_layers']
        if not isinstance(nets, dict) or not nets:
            raise RequestError('per_net_signal_layers must map net names to allowed layers')
        for net, layers in nets.items():
            if not isinstance(net, str) or not NET_NAME.fullmatch(net):
                raise RequestError('per_net_signal_layers keys must be net names')
            _names(layers, COPPER_LAYER, 'per-net signal layers')
            if not set(layers) <= set(policy['signal_layers']):
                raise RequestError('per-net signal layers must be within global signal_layers')
    if 'layer_costs' in policy:
        costs = policy['layer_costs']
        if (not isinstance(costs, list) or len(costs) != len(policy['signal_layers']) or
                not all(_number(c, 1, 1000) for c in costs)):
            raise RequestError('layer_costs must give one multiplier from 1 to 1000 per signal layer')
    if not _number(policy['pour_area_loss_pct'], 0, 100):
        raise RequestError('pour_area_loss_pct must be a number from 0 to 100')
    if 'reference' in policy:
        ref = policy['reference']
        if not isinstance(ref, dict) or set(ref) != REFERENCE_KEYS:
            raise RequestError('reference must have exactly net, layers and rf_nets')
        if not isinstance(ref['net'], str) or not NET_NAME.match(ref['net']):
            raise RequestError('reference net must be a net name')
        _names(ref['layers'], COPPER_LAYER, 'reference layers')
        _names(ref['rf_nets'], NET_NAME, 'reference rf_nets')
        if ref['net'] in ref['rf_nets']:
            raise RequestError('the reference net cannot be one of its RF nets')
    return policy


def parse_layer_policy(text):
    try:
        policy = json.loads(text)
    except (TypeError, ValueError, RecursionError):
        raise RequestError('layer policy must be JSON') from None
    validate_layer_policy(policy)
    if canonical_json(policy) != text:
        raise RequestError('layer policy must be canonical JSON (sorted keys, no spaces)')
    return policy


def _unique_names(value):
    return (isinstance(value, list) and bool(value) and
            all(isinstance(v, str) and v for v in value) and len(value) == len(set(value)))


def strict_json(data, label):
    """JSON text or bytes with unique keys, finite numbers and bounded nesting, or RequestError.

    The same strictness as kicad_route.experiment_policy.load: a repeated key is
    ambiguous evidence, never silently resolved to its last value. NaN,
    Infinity, a float that overflows to infinity, an integer beyond the
    interpreter's digit limit and nesting beyond EXPERIMENT_JSON_DEPTH are
    malformed evidence too, never an escaping exception."""
    def unique(pairs):
        found = {}
        for key, value in pairs:
            if key in found:
                raise RequestError(f'{label} has a duplicate key {json.dumps(key)}')
            found[key] = value
        return found

    def constant(name):
        raise RequestError(f'{label} has a non-finite number {name}')

    def finite(token):
        value = float(token)
        if not math.isfinite(value):
            raise RequestError(f'{label} has a non-finite number {token[:32]}')
        return value
    try:
        value = json.loads(data, object_pairs_hook=unique, parse_constant=constant, parse_float=finite)
    except RequestError:
        raise
    except RecursionError:
        raise RequestError(f'{label} nesting exceeds {EXPERIMENT_JSON_DEPTH}') from None
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
        raise RequestError(f'{label} must be JSON') from None
    except ValueError:
        # For example an integer beyond the interpreter's digit limit.
        raise RequestError(f'{label} has an out-of-range number') from None
    stack = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if isinstance(item, (dict, list)):
            if depth > EXPERIMENT_JSON_DEPTH:
                raise RequestError(f'{label} nesting exceeds {EXPERIMENT_JSON_DEPTH}')
            stack.extend((v, depth + 1) for v in (item.values() if isinstance(item, dict) else item))
    return value


def strict_json_file(path, label):
    """strict_json of a retained regular file within JSON_LIMIT, or RequestError."""
    if not path.is_file() or path.is_symlink():
        raise RequestError(f'{label} is not a regular file')
    if path.stat().st_size > JSON_LIMIT:
        raise RequestError(f'{label} exceeds {JSON_LIMIT} bytes')
    return strict_json(path.read_bytes(), label)


def validate_experiment_policy(policy, label='experiment policy'):
    """A routability-policy/1 document, unchanged, or RequestError.

    Exactly the five fields kicad_route.experiment_policy.load accepts, with its
    value rules; any other schema version, class, key or shape (including the
    two-field {schema, policy} draft) is refused. Whether rule_areas_sha256
    matches the board's native rule areas is the router's qualification, which
    agent-ee does not claim."""
    if not isinstance(policy, dict) or set(policy) != EXPERIMENT_POLICY_KEYS:
        raise RequestError(f'{label} must have exactly the routability-policy/1 fields '
                           + ', '.join(sorted(EXPERIMENT_POLICY_KEYS)))
    if policy['schema'] != EXPERIMENT_POLICY_SCHEMA:
        raise RequestError(f'incompatible {label} schema: expected {EXPERIMENT_POLICY_SCHEMA}')
    if policy['experiment_class'] not in EXPERIMENT_CLASSES:
        raise RequestError(f'experiment_class must be one of {EXPERIMENT_CLASSES}')
    if not _unique_names(policy['allowed_trace_layers']):
        raise RequestError('allowed_trace_layers must be unique nonempty layer names')
    spans = policy['allowed_via_spans']
    if not isinstance(spans, list) or any(not _unique_names(s) or len(s) != 2 for s in spans):
        raise RequestError('allowed_via_spans must contain distinct endpoint pairs')
    if len({tuple(s) for s in spans}) != len(spans):
        raise RequestError('duplicate allowed_via_spans')
    if not isinstance(policy['rule_areas_sha256'], str) or not SHA256.match(policy['rule_areas_sha256']):
        raise RequestError('rule_areas_sha256 must be 64 lowercase hex digits')
    return policy


def parse_experiment_policy(text):
    """A declared routability-policy/1 document, unchanged, or RequestError.

    validate_experiment_policy of strict canonical JSON; refused before the
    router runs."""
    policy = validate_experiment_policy(strict_json(text, 'experiment policy'))
    if canonical_json(policy) != text:
        raise RequestError('experiment policy must be canonical JSON (sorted keys, no spaces)')
    return policy


def experiment_policy_identity(policy):
    """The identity of the declaration the adapter writes for the router, or None.

    sha256 is of the canonical file bytes; policy_sha256 is kicad-route's
    common.sha256_of(policy), the digest experiment_policy.validate reports."""
    if policy is None:
        return None
    return {'declared': policy, 'sha256': hashlib.sha256(canonical_json(policy).encode()).hexdigest(),
            'policy_sha256': hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()}


def retain_experiment(run_dir, routing):
    """Copy the root sidecar and the router's policy copy into routing/attempt.

    Both are run-relative root files, copied whether or not result.json names
    them, so experiment_grade can grade or refuse them from the retained bytes.
    Returns the retained names and those that are links or special files."""
    kept, links = [], []
    if run_dir is None:
        return kept, links
    for name in (EXPERIMENT_SIDECAR, EXPERIMENT_POLICY_COPY):
        src = Path(run_dir) / name
        if src.is_symlink() or (os.path.lexists(src) and not src.is_file()):
            links.append(name)
        elif src.is_file():
            shutil.copyfile(src, routing / 'attempt' / name)
            kept.append(name)
    return kept, links


def _retained_file(attempt_dir, name):
    path = Path(attempt_dir) / name
    if path.is_symlink() or not path.is_file():
        return None
    return {'path': f'attempt/{name}', 'sha256': digest(path)}


def experiment_grade(request, result, attempt_dir, run=None):
    """The experiment-evidence record, re-derived from the retained bytes alone.

    None when no policy was declared, result.json references no sidecar and
    neither root file was retained. Otherwise 'retained' with the sidecar's
    recomputed identity, 'refused' with one of EXPERIMENT_REFUSALS, or 'absent'
    when the router wrote neither result.json nor a sidecar. The reference binds
    only the run-relative EXPERIMENT_SIDECAR; a retained root file that no valid
    reference binds is refused as 'unbound' with its recomputed identity. A
    sidecar that claims comparison_eligible is requalified by _requalify from
    the retained attempt record, facts and sealed manifests it names, bound to
    the execution run (experiment_run), and refused (typed) when that proof is
    missing, unsupported or another run's.
    Every record is qualified: false."""
    expected = experiment_policy_identity(request.get('experiment_policy'))
    has_ref = isinstance(result, dict) and 'experiment' in result
    orphan = _retained_file(attempt_dir, EXPERIMENT_SIDECAR)
    copy = _retained_file(attempt_dir, EXPERIMENT_POLICY_COPY)
    if expected is None and not has_ref and orphan is None and copy is None:
        return None
    base = {'qualified': False, 'contract': EXPERIMENT_CONTRACT, 'policy': expected,
            'policy_file': f'attempt/{EXPERIMENT_POLICY_FILE}' if expected is not None else None,
            'policy_copy': copy}
    ref = result.get('experiment') if isinstance(result, dict) else None
    try:
        canonical_json(ref)
        echo = ref
    except (ValueError, RecursionError):
        # A non-finite number the permissive result.json read decoded: not echoed.
        echo = None

    def refused(reason, detail, **known):
        return {'status': 'refused', **base, 'reason': reason, 'detail': detail,
                'reference': echo, 'path': None, 'sha256': None, **known}

    unbound = orphan or copy
    unbound_name = EXPERIMENT_SIDECAR if orphan is not None else EXPERIMENT_POLICY_COPY
    if not isinstance(result, dict):
        if unbound is not None:
            return refused('unbound', f'{unbound_name} exists but there is no result.json to bind it', **unbound)
        return {'status': 'absent', **base, 'reason': 'no result.json: the router reported no experiment'}
    if not has_ref:
        if unbound is not None:
            return refused('unbound', f'{unbound_name} exists but result.json has no experiment reference',
                           **unbound)
        return refused('missing', 'a policy was declared but result.json has no experiment reference')
    if echo is not ref:
        return refused('malformed', 'experiment reference has a non-finite number or excessive nesting',
                       **(orphan or {}))
    if (not isinstance(ref, dict) or set(ref) != EXPERIMENT_REFERENCE_KEYS or
            not all(isinstance(ref[k], str) for k in EXPERIMENT_REFERENCE_KEYS)):
        return refused('malformed', 'experiment reference must have exactly string path, sha256 and schema',
                       **(orphan or {}))
    ambiguous = _ambiguous_binding(Path(attempt_dir) / 'result.json')
    if ambiguous:
        return refused('malformed', ambiguous, **(orphan or {}))
    if ref['schema'] != EXPERIMENT_SCHEMA:
        return refused('unknown_schema', f'experiment reference schema is not {EXPERIMENT_SCHEMA}',
                       **(orphan or {}))
    if not SHA256.match(ref['sha256']):
        return refused('malformed', 'experiment reference sha256 is not 64 lowercase hex digits',
                       **(orphan or {}))
    if ref['path'] != EXPERIMENT_SIDECAR:
        # Only the run-relative root sidecar binds; a cwd-relative, absolute or
        # other path is unsupported and must not look like bound evidence.
        return refused('malformed', f'experiment path is not the run-relative {EXPERIMENT_SIDECAR}',
                       **(orphan or {}))
    if orphan is None:
        return refused('missing', 'no regular sidecar file at the referenced path was retained')
    if orphan['sha256'] != ref['sha256']:
        return refused('tampered', 'sidecar sha256 differs from the result.json reference', **orphan)
    try:
        body = strict_json_file(Path(attempt_dir) / EXPERIMENT_SIDECAR, 'experiment sidecar')
    except RequestError as exc:
        return refused('malformed', str(exc), **orphan)
    if not isinstance(body, dict):
        return refused('malformed', 'experiment sidecar is not a JSON object', **orphan)
    if body.get('schema') != EXPERIMENT_SCHEMA:
        return refused('incompatible_schema', f'sidecar schema is not {EXPERIMENT_SCHEMA}', **orphan)
    shape = _sidecar_shape_refusal(body)
    if shape is not None:
        return refused(*shape, **orphan)
    identity = body['identity']
    claimed = identity['policy']
    if not isinstance(claimed, dict) or not EXPERIMENT_SIDECAR_POLICY_KEYS <= set(claimed):
        return refused('malformed', 'sidecar has no identity.policy {declared, declared_sha256, experiment_class}',
                       **orphan)
    declared = None if expected is None else expected['declared']
    # Canonical bytes, not decoded equality: 1 == 1.0 == True in Python.
    if (canonical_json(claimed['declared']) != canonical_json(declared) or
            canonical_json(claimed['declared_sha256']) !=
            canonical_json(None if expected is None else expected['policy_sha256']) or
            canonical_json(claimed['experiment_class']) !=
            canonical_json(None if declared is None else declared['experiment_class'])):
        return refused('incompatible_policy', 'sidecar policy identity differs from the declared policy', **orphan)
    bound = body['artifacts'].get(EXPERIMENT_POLICY_COPY)
    if copy is not None:
        refusal = _policy_copy_refusal(Path(attempt_dir) / EXPERIMENT_POLICY_COPY, declared)
        if refusal is not None:
            return refused(*refusal, **orphan)
        if bound != copy['sha256']:
            return refused('tampered', f'{EXPERIMENT_POLICY_COPY} sha256 differs from the sidecar artifacts digest',
                           **orphan)
    elif bound is not None:
        return refused('missing', f'the sidecar binds {EXPERIMENT_POLICY_COPY} but no regular copy was retained',
                       **orphan)
    eligibility = {'identity_sha256': body['identity_sha256'], 'comparison_eligible': body['comparison_eligible'],
                   'ineligible_reasons': body['ineligible_reasons']}
    if expected is not None and body['comparison_eligible'] is not True:
        return refused('ineligible', 'the producer marked the declared experiment not comparison-eligible: '
                       + '; '.join(body['ineligible_reasons'][:8]), **orphan, **eligibility)
    if body['comparison_eligible']:
        unqualified = _requalify(body, result, attempt_dir, run)
        if unqualified is not None:
            return refused(*unqualified, **orphan, **eligibility)
    return {'status': 'retained', **base, 'schema': EXPERIMENT_SCHEMA, 'reference': ref, **orphan, **eligibility}


def _sidecar_shape_refusal(body):
    """(reason, detail) refusing a sidecar that is not kicad-route's routability-experiment/1, or None.

    The top-level and identity key sets are exact (the producer schema forbids
    additional properties); identity_sha256 must be kicad-route's
    common.sha256_of(identity), so an identity edited after it was sealed is
    tampered even when result.json re-binds the edited file."""
    if set(body) != EXPERIMENT_SIDECAR_KEYS:
        return 'malformed', 'sidecar must have exactly the fields ' + ', '.join(sorted(EXPERIMENT_SIDECAR_KEYS))
    identity = body['identity']
    if not isinstance(identity, dict) or set(identity) != EXPERIMENT_IDENTITY_KEYS:
        return 'malformed', 'sidecar identity must have exactly ' + ', '.join(sorted(EXPERIMENT_IDENTITY_KEYS))
    if body['replay_authority'] is not False:
        return 'malformed', 'sidecar replay_authority must be false'
    reasons = body['ineligible_reasons']
    if (not isinstance(body['comparison_eligible'], bool) or not isinstance(reasons, list) or
            not all(isinstance(r, str) for r in reasons) or body['comparison_eligible'] == bool(reasons)):
        return 'malformed', 'sidecar comparison_eligible must be a boolean that is true exactly without reasons'
    artifacts = body['artifacts']
    if not isinstance(artifacts, dict) or any(v is not None and (not isinstance(v, str) or not SHA256.match(v))
                                              for v in artifacts.values()):
        return 'malformed', 'sidecar artifacts must map names to sha256 digests or null'
    if not isinstance(body['identity_sha256'], str) or not SHA256.match(body['identity_sha256']):
        return 'malformed', 'sidecar identity_sha256 is not 64 lowercase hex digits'
    error = _shape_error(body, EXPERIMENT_SHAPE)
    if error is not None:
        return 'malformed', f'{error} (kicad-route routability-experiment/1)'
    if body['identity_sha256'] != _sha256_of(identity):
        return 'tampered', 'sidecar identity_sha256 differs from the digest of its identity'
    return None


def _sha256_of(value):
    """kicad-route common.sha256_of: the digest of the sorted-key json.dumps text."""
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _retained_json(attempt_dir, name, label=None):
    """(value, None) for a retained regular strict-JSON file, else (None, (reason, detail))."""
    path = Path(attempt_dir) / name
    if path.is_symlink() or not path.is_file():
        return None, ('missing', f'no regular {name} was retained')
    try:
        return strict_json_file(path, label or name), None
    except RequestError as exc:
        return None, ('malformed', str(exc))


def experiment_run(request, layout, attempt_dir, omitted=None):
    """The execution an eligible sidecar must describe, or None without a discovered run.

    attempt_dir is the discovered run relative to routing/ (report.discovery);
    the paths are those the sandboxed router was given (tool_argv's --out and
    --dir), so the sidecar is bound to this execution, never to its own claims.
    omitted is the adapter's own record of the design files retain() observed
    in the run and did not copy (report.omitted_design_files): the only digests
    of the raw backend output and of the prepared backend input (work/input)
    that are not the producer's claims."""
    if not isinstance(attempt_dir, str):
        return None
    return {'run_dir': str(Path(layout['work']) / 'routing' / attempt_dir),
            'source_dir': str(staged_dir(request, layout)),
            'observed': dict(omitted) if isinstance(omitted, dict) else {}}


def _bound_artifacts(sealed, attempt_dir, observed):
    """(reason, detail) unless every artifact the sidecar names is the retained file it names.

    Each non-null digest must name a run-relative report retain() copies and be
    recomputed from that retained file; the attempt record, both facts files,
    the sealed manifests and the raw backend output must be named
    (EXPERIMENT_BOUND). The raw output is a design file, never retained: its
    digest must be the one the adapter observed in this run (observed, from
    experiment_run). No other design file can be bound. The adapter's own files
    are never the identity source: an unnamed one is unbound evidence."""
    for name, want in sorted(sealed.items()):
        if name == EXPERIMENT_POLICY_COPY or want is None:
            continue
        parts = name.split('/')
        if (any(p in ('', '.', '..') for p in parts) or
                not (name in RETAINED_FILES or (len(parts) > 1 and parts[0] in RETAINED_DIRS))):
            return 'malformed', f'sidecar artifact {name!r} is not a run-relative retained report'
        if name == EXPERIMENT_RAW_OUTPUT:
            if name not in observed:
                return 'missing', f'the sidecar binds {name} but the adapter observed no such regular file'
            if observed[name] != want:
                return 'tampered', f'{name} sha256 differs from the sidecar artifacts digest'
            continue
        if name.endswith(DESIGN_SUFFIXES):
            return 'malformed', f'sidecar artifact {name!r} is a design file, which is never retained'
        kept = _retained_file(attempt_dir, name)
        if kept is None:
            return 'missing', f'the sidecar binds {name} but no regular file was retained'
        if kept['sha256'] != want:
            return 'tampered', f'{name} sha256 differs from the sidecar artifacts digest'
    for name in EXPERIMENT_BOUND:
        if sealed.get(name) is None:
            return 'unbound', f'the eligible sidecar binds no {name} digest'
    return None


def _requalify(body, result, attempt_dir, run):
    """(reason, detail) refusing an eligible-claiming sidecar this boundary cannot requalify, or None.

    The producer's comparison_eligible flag and the stored 'bound' status are
    claims, not authority (review H1, ee442e). First every artifact the sidecar
    names is bound by digest to the retained file (_bound_artifacts), so the
    checks below read only the bytes the sidecar describes. Then the obligations
    are re-derived from them: the attempt record and the native facts are JSON
    objects of the producer's shape (EI4); the effective-configuration manifest
    covers exactly one outer batch and no later subrun, its capture is complete,
    it binds the retained request by hash, and the sidecar identity and the
    result.json process binding restate it (EI2); and the origin is a single run
    whose input is not its own output (EI3). Last, the sidecar must describe this
    execution (run, an experiment_run): its input, source, request, candidate,
    outcome and output are this attempt's, else 'run_mismatch'. A predecessor
    chain is unsupported here: its receipts are not retained."""
    sealed = body['artifacts']
    refusal = _bound_artifacts(sealed, attempt_dir, (run or {}).get('observed') or {})
    if refusal is not None:
        return refusal
    record, refusal = _retained_json(attempt_dir, 'attempt.json')
    if refusal is not None:
        return refusal
    source = record.get('source') if isinstance(record, dict) else None
    if (not isinstance(record, dict) or not isinstance(record.get('schema'), str) or
            not record['schema'].startswith('kicad-route/attempt/') or
            not all(isinstance(record.get(k), str) and record[k] for k in ('board', 'candidate_dir',
                                                                           'input_fingerprint')) or
            not isinstance(source, dict) or not isinstance(source.get('source_dir'), str) or
            not isinstance(record.get('settings'), dict)):
        return 'malformed', ('attempt.json is not a kicad-route attempt record (an object with schema, board, '
                             'candidate_dir, input_fingerprint and source.source_dir strings and settings)')
    facts = {}
    for name, lists in (('facts/reference-facts.json', ('tracks', 'vias', 'copper_layers')),
                        ('validate/candidate-facts.json', ('tracks', 'vias'))):
        facts[name], refusal = _retained_json(attempt_dir, name)
        if refusal is not None:
            return refusal
        found = facts[name]
        if (not isinstance(found, dict) or
                any(k in found and (not isinstance(found[k], list) or
                                    not all(isinstance(v, dict) for v in found[k])) for k in lists) or
                ('footprints' in found and not isinstance(found['footprints'], dict))):
            return 'malformed', f'{name} is not a native facts object (lists of objects, footprints an object)'
    manifest = {}
    for name in EXPERIMENT_MANIFESTS:
        manifest[name], refusal = _retained_json(attempt_dir, name)
        if refusal is not None:
            return refusal
        if not isinstance(manifest[name], dict):
            return 'malformed', f'{name} is not a JSON object'
    config, request = (manifest[n] for n in EXPERIMENT_MANIFESTS)
    config_name, request_name = EXPERIMENT_MANIFESTS
    if request.get('schema') != EXPERIMENT_REQUEST_SCHEMA:
        return 'incompatible_schema', f'{request_name} schema is not {EXPERIMENT_REQUEST_SCHEMA}'
    if config.get('schema') != EXPERIMENT_MANIFEST_SCHEMA:
        return 'incompatible_schema', f'{config_name} schema is not {EXPERIMENT_MANIFEST_SCHEMA}'
    if config.get('capture_status') != 'complete' or config.get('issues') != []:
        return 'unqualified', f'{config_name} capture is not complete without issues'
    if config.get('coverage') != EXPERIMENT_COVERAGE:
        return 'unqualified', f'{config_name} coverage is not {EXPERIMENT_COVERAGE}'
    if canonical_json(config.get('execution')) != canonical_json(EXPERIMENT_EXECUTION):
        return 'unqualified', (f'{config_name} execution is not one outer batch without later subruns: '
                               + canonical_json(config.get('execution')))
    if config.get('request_sha256') != sealed[request_name]:
        return 'unqualified', f'{config_name} request_sha256 is not the retained {request_name}'
    for key in ('source_sha256', 'producer_sha256'):
        if not isinstance(config.get(key), str) or config[key] != request.get(key):
            return 'unqualified', f'{config_name} {key} differs from {request_name}'
    identity = body['identity']
    effective, build = identity['effective'], identity['build']
    restated = {**{k: config.get(k) for k in EXPERIMENT_RESTATED},
                'rules_sha256': _sha256_of(config['rules']) if config.get('rules') is not None else None,
                'net_inventory_sha256': _sha256_of(config['net_inventory']) if config.get('net_inventory') else None}
    if (effective is None or any(canonical_json(effective[k]) != canonical_json(v) for k, v in restated.items()) or
            build['executed_source_sha256'] != config['source_sha256'] or
            build['producer_sha256'] != config['producer_sha256']):
        return 'unqualified', f'the sidecar identity does not restate the retained {config_name}'
    process = result.get('process')
    binding = process.get('effective_configuration') if isinstance(process, dict) else None
    if (not isinstance(binding, dict) or binding.get('status') != 'bound' or
            binding.get('sha256') != sealed[config_name] or binding.get('request_sha256') != sealed[request_name] or
            binding.get('capture_status') != 'complete' or binding.get('coverage') != EXPERIMENT_COVERAGE):
        return 'unqualified', f'result.json process.effective_configuration does not bind the retained {config_name}'
    origin, claimed = identity['origin'], identity['input']['fingerprint']
    if origin['depth'] != 0 or origin['predecessors']:
        return 'unqualified', (f'the origin chain has {origin["depth"]} predecessor(s) whose receipts this '
                               'boundary does not retain')
    if origin['root_input_fingerprint'] is None or origin['root_input_fingerprint'] != claimed:
        return 'unqualified', 'origin root_input_fingerprint is not the sidecar input fingerprint'
    if run is None:
        return 'run_mismatch', 'no discovered run to bind the eligible sidecar to'
    # kicad-route cli.previous_step resolves source_dir and stops at a revisit of this run,
    # reporting depth 0 (EI3). The path is a producer-namespace string: it is normalised
    # lexically (., .., repeated and trailing separators), never followed on this host.
    # Any form inside this run is a self-origin, whatever alias reaches it; any other
    # string than the exact input directory the adapter passed is not this execution.
    src = source['source_dir']
    run_dir = PurePosixPath(run['run_dir'])
    canon = PurePosixPath(posixpath.normpath('/' + src.lstrip('/'))) if src.startswith('/') else None
    if canon is not None and (canon == run_dir or run_dir in canon.parents):
        return 'unqualified', ("origin is a self-cycle: attempt.json source_dir is this run's own candidate "
                               f'or another path inside this run ({src})')
    if src != run['source_dir']:
        return 'run_mismatch', f'attempt.json source_dir {src!r} is not this attempt\'s input {run["source_dir"]}'
    return _run_mismatch(body, result, record, facts, config, request, attempt_dir, run_dir,
                         run.get('observed') or {})


def _rules_inventory(files, board):
    """kicad-route a0ed434 cli.rules_files over the typed snapshot: (project, custom_rules,
    not_loaded) KiCad grades board with, derived from file presence, never from a claim.

    KiCad loads only the project and custom rules of the board's own name; every other
    .kicad_pro/.kicad_dru beside the board is not loaded. The snapshot records the
    reference copy rules_files reads, by run-relative name."""
    path = PurePosixPath(board)
    project, rules = path.with_suffix('.kicad_pro').name, path.with_suffix('.kicad_dru').name
    beside = [PurePosixPath(n) for n in files if PurePosixPath(n).parent == path.parent]
    return (project, rules if str(path.with_suffix('.kicad_dru')) in files else None,
            sorted(p.name for p in beside if p.suffix in ('.kicad_pro', '.kicad_dru') and
                   p.name not in (project, rules)))


def _prepared_input(record, board, observed):
    """(prepared, refusal): the prepared backend input the adapter observed (kicad-route
    a0ed434 experiment._retained_backend_input): work/input/<reference name>/<board> and
    its .kicad_pro and .kicad_dru, by the digests retain() recorded.

    Without a regular prepared board and project, prepared is None. The optional
    .kicad_dru is null only when retain() observed no entry: a link (valid or dangling)
    or special file there is neither absent nor regular, and is refused without being
    followed, as the producer's input guard rejects it (agent-510 R4)."""
    reference = record.get('reference_dir')
    name = PurePosixPath(reference).name if isinstance(reference, str) else ''
    if name in ('', '.', '..') or PurePosixPath(board).name != board:
        return None, None
    path = PurePosixPath('work/input', name, board)

    def regular(p):
        value = observed.get(str(p))
        return value if isinstance(value, str) and SHA256.match(value) else None
    prepared = {'board_sha256': regular(path),
                'rules': {s: regular(path.with_suffix(s)) for s in ('.kicad_pro', '.kicad_dru')}}
    if not (prepared['board_sha256'] and prepared['rules']['.kicad_pro']):
        return None, None
    rules = str(path.with_suffix('.kicad_dru'))
    if rules in observed and prepared['rules']['.kicad_dru'] is None:
        kind = {'link': 'link', 'special': 'directory or special file'}.get(observed[rules], 'unrecognised entry')
        return None, f'the prepared {rules} is a {kind}, neither absent nor a regular file'
    return prepared, None


def _run_mismatch(body, result, record, facts, config, request, attempt_dir, run_dir, observed):
    """(reason, detail) unless the sidecar describes this execution, or None.

    Every comparison is with retained bytes the adapter binds independently:
    result.json (whose input, settings, validation and run the adapter checks
    against the request and the delivered board), snapshot.json, and the attempt
    record, facts and manifests the sidecar names. Two input stages stay distinct:
    the ORIGINAL input (identity.input) is the snapshot, with the rules inventory
    derived from it (_rules_inventory); the PREPARED input (identity.effective.input,
    both manifests' input and the binding's captured_input_sha256) is the
    work/input the adapter observed (_prepared_input), which protection and
    preprocessing may change. A self-consistent sidecar of another run, even with
    digests rewritten to this run's files, differs here ('run_mismatch'); a
    snapshot.json that is not a file-digest map is 'missing' or 'malformed',
    never a crash."""
    identity, candidate, outcome, sealed = body['identity'], body['candidate'], body['outcome'], body['artifacts']
    claimed = identity['input']
    inp = result.get('input') if isinstance(result.get('input'), dict) else {}
    snapshot, refusal = _retained_json(attempt_dir, 'snapshot.json')
    if refusal is not None:
        return refusal
    files = snapshot.get('files') if isinstance(snapshot, dict) else None
    if (not isinstance(files, dict) or not files or
            not all(isinstance(v, str) and SHA256.match(v) for v in files.values())):
        return 'malformed', 'snapshot.json files is not a non-empty map of file names to sha256 digests'
    board = record['board']
    # Review H1 (ee442j): the attempt record's board is untrusted. kicad-route grades a
    # board by a plain file name in the input directory; an empty, dot or separated name
    # ('.', '..', '/', 'sub/b', 'b/') is not that input, and is refused before any name
    # is derived from it (PurePosixPath('.').with_suffix raises).
    if board in ('.', '..') or PurePosixPath(board).name != board:
        return 'run_mismatch', (f'attempt.json board {board!r} is not a plain file name: kicad-route '
                                'refuses such an input')
    # The named input files (review H1, ee442g; agent-510 R1): kicad-route names the
    # board's own project file and custom rules file, digests from the reference copy,
    # which snapshot.json records. Which rules file is loaded, and which siblings are
    # not, is derived from the snapshot; the attempt record and the sidecar must both
    # state that one inventory. A name or digest of another input is not this execution's.
    rules_files = record.get('rules_files')
    if not isinstance(rules_files, dict) or not {'custom_rules', 'not_loaded'} <= set(rules_files):
        return 'run_mismatch', 'attempt.json rules_files does not state custom_rules and not_loaded'
    project_name, custom, not_loaded = _rules_inventory(files, board)
    if project_name not in files:
        return 'run_mismatch', f'the snapshot has no {project_name}: kicad-route refuses such an input'
    # agent-510 R2: the prepared input is observed in work/input, not the snapshot.
    prepared, unresolved = _prepared_input(record, board, observed)
    if unresolved is not None:
        return 'run_mismatch', unresolved
    if prepared is None:
        return 'run_mismatch', ('the adapter observed no regular prepared input board and project in work/input '
                                'for attempt.json reference_dir and board')
    checks = [
        ('input fingerprint', claimed['fingerprint'], [record['input_fingerprint'], inp.get('fingerprint'),
                                                       fingerprint(files)]),
        ('input snapshot files', claimed['snapshot_files'], [files, claimed['files']]),
        ('input board', [claimed['board']['name'], claimed['board']['sha256']],
         [[inp.get('board'), files.get(board)], [board, facts['facts/reference-facts.json'].get('sha256')]]),
        ('input project', [claimed['project']['name'], claimed['project']['sha256']],
         [[project_name, files.get(project_name)]]),
        ('input rules file', [rules_files.get('project'), rules_files['custom_rules'],
                              rules_files['not_loaded']], [[project_name, custom, not_loaded]]),
        ('input rules', [claimed['rules']['name'], claimed['rules']['sha256'], claimed['rules'].get('not_loaded')],
         [[custom, files.get(custom) if custom is not None else None, not_loaded]]),
        ('effective input', identity['effective']['input'], [prepared, config.get('input'), request.get('input')]),
        ('input source', claimed['source'], [record['source']])]
    settings = record['settings']
    requested = identity['request']
    checks += [(f'request {k}', requested.get(k, _UNSTATED), [settings.get(k, _UNSTATED)])
               for k in EXPERIMENT_REQUEST_SETTINGS]
    checks += [('request backend', canonical_backend(requested.get('backend')),
                [canonical_backend(settings.get('backend'))]),
               ('request experiment_policy_sha256', requested.get('experiment_policy_sha256'),
                [identity['policy']['declared_sha256']]),
               ('protection mode', identity['protection']['mode'], [settings.get('protect')]),
               ('backend budget', identity['budgets']['backend_s'], [settings.get('budget_s')])]
    validation = result.get('validation') if isinstance(result.get('validation'), dict) else {}
    binding = result['process']['effective_configuration']
    output = config.get('output') if isinstance(config.get('output'), dict) else {}
    sha = validation.get('validated_board_sha256')
    # The prepared input and three distinct output seals, each against its own stage
    # record (kicad-route a0ed434).
    checks += [('captured input board', prepared['board_sha256'], [binding.get('captured_input_sha256')]),
               ('raw backend output board', candidate['raw_backend_output_board_sha256'],
                [sealed.get(EXPERIMENT_RAW_OUTPUT), output.get('board_sha256'), binding.get('backend_output_sha256')]),
               ('raw backend output stage', EXPERIMENT_RAW_STAGE, [output.get('stage'), binding.get('output_stage')]),
               ('wrapper output board', [candidate['backend_output_board_sha256'], candidate['pre_refill_board_sha256']],
                [[binding.get('wrapper_output_board_sha256')] * 2, [validation.get('delivered_board_sha256')] * 2]),
               ('wrapper output stage', EXPERIMENT_WRAPPER_STAGE, [binding.get('wrapper_output_stage')]),
               ('graded refilled board', [candidate[k] for k in ('candidate_board_sha256', 'validated_board_sha256',
                                                                 'file_sha256')],
                [[sha, validation.get('candidate_board_sha256'), facts['validate/candidate-facts.json'].get('sha256')]
                 if isinstance(sha, str) else None])]
    statuses = {k: result[k].get('status') if isinstance(result.get(k), dict) else None
                for k in ('process', 'routing', 'validation')}
    checks.append(('outcome', {k: outcome[k] for k in statuses}, [statuses]))
    requested_output = config.get('requested_output')
    checks.append(('requested output',
                   posixpath.normpath(requested_output) if isinstance(requested_output, str) else None,
                   [str(run_dir / 'work/krt-out' / board)]))
    for label, claim, observed in checks:
        # A request setting may be null but must be stated; every other claim must be non-null.
        if (claim is None and not label.startswith('request ')) or any(
                v is _UNSTATED for v in (claim, *observed)) or any(
                canonical_json(claim) != canonical_json(o) for o in observed):
            return 'run_mismatch', f'the sidecar {label} is not this execution\'s'
    return None


def _ambiguous_binding(result_path):
    """Why result.json's experiment binding is ambiguous, or None.

    The permissive result.json read keeps the last of repeated keys, so the
    binding is re-read strictly: a repeated top-level experiment key, or a
    repeated key inside the reference, refuses the evidence."""
    marked = []

    def pairs_hook(pairs):
        found = {}
        for key, value in pairs:
            if key in found:
                marked.append((found, key))
            found[key] = value
        return found
    try:
        top = json.loads(result_path.read_bytes(), object_pairs_hook=pairs_hook)
    except (OSError, UnicodeDecodeError, ValueError, RecursionError):
        return 'result.json is not JSON'
    ref = top.get('experiment') if isinstance(top, dict) else None
    if any(obj is top and key == 'experiment' for obj, key in marked):
        return 'result.json has a duplicate key "experiment"'
    if any(obj is ref for obj, _ in marked):
        return 'experiment reference has a duplicate key'
    return None


def _policy_copy_refusal(path, declared):
    """(reason, detail) refusing the router's retained policy copy, or None.

    The copy is read like kicad_route.experiment_policy.load (duplicate keys
    refused, exactly the five routability-policy/1 fields), must declare the
    same policy and must be the bytes kicad-route's common.write_json writes for
    it (json.dumps(policy, indent=2) + newline, in the copy's own key order)."""
    try:
        kept = validate_experiment_policy(strict_json_file(path, EXPERIMENT_POLICY_COPY), EXPERIMENT_POLICY_COPY)
    except RequestError as exc:
        return 'malformed', str(exc)
    if declared is None or canonical_json(kept) != canonical_json(declared):
        return 'incompatible_policy', f'{EXPERIMENT_POLICY_COPY} differs from the declared policy'
    if path.read_bytes() != (json.dumps(kept, indent=2) + '\n').encode():
        return 'malformed', f'{EXPERIMENT_POLICY_COPY} is not the bytes kicad-route writes for the policy'
    return None


def policy_backend_args(policy):
    """KRT's own --layers/--layer-costs, as --backend-arg values. A board copper layer
    not listed is appended by KRT at FORBIDDEN cost: an obstacle with no track copper."""
    args = ['--layers', *policy['signal_layers']]
    if 'layer_costs' in policy:
        args += ['--layer-costs', *(repr(float(c)) for c in policy['layer_costs'])]
    return args


def digest(path):
    h = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            h.update(block)
    return h.hexdigest()


def canonical_identity(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def tree(root):
    """Every entry under root without following links: files by sha256 and mode,
    links by target, directories and special files by kind."""
    root = Path(root)
    entries = {}
    if root.is_symlink() or not root.is_dir():
        raise ValueError(f'not a directory: {root}')
    for current, dirs, names in os.walk(root, followlinks=False):
        base = Path(current)
        for name in sorted(dirs + names):
            path = base / name
            rel = path.relative_to(root).as_posix()
            st = path.lstat()
            if path.is_symlink():
                entries[rel] = ['link', os.readlink(path)]
            elif path.is_dir():
                entries[rel] = ['dir', oct(st.st_mode & 0o7777)]
            elif path.is_file():
                entries[rel] = ['file', digest(path), oct(st.st_mode & 0o7777)]
            else:
                entries[rel] = ['other', oct(st.st_mode)]
        dirs[:] = [d for d in dirs if not (base / d).is_symlink()]
    return dict(sorted(entries.items()))


def tree_identity(root):
    entries = tree(root)
    return {'sha256': canonical_identity(entries), 'entries': len(entries)}


def files_of(entries):
    return {rel: e[1] for rel, e in entries.items() if e[0] == 'file'}


def links_of(entries):
    return sorted(rel for rel, e in entries.items() if e[0] in ('link', 'other'))


def checkout_commit(checkout):
    """HEAD commit from the git files themselves; running git could write the index."""
    git = Path(checkout) / '.git'
    head = (git / 'HEAD').read_text().strip()
    if not head.startswith('ref: '):
        return head
    ref = head[5:]
    loose = git / ref
    if loose.is_file():
        return loose.read_text().strip()
    packed = git / 'packed-refs'
    if packed.is_file():
        for line in packed.read_text().splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[1] == ref:
                return parts[0]
    return None


def verify_code(checkout, pinned=None):
    """Problems with the mounted tool code relative to the pinned blob hashes."""
    checkout, pinned = Path(checkout), PINNED_CODE if pinned is None else pinned
    problems, seen = [], {}
    for top in ('bin', 'src'):
        if not (checkout / top).is_dir() or (checkout / top).is_symlink():
            problems.append(f'missing tool directory {top}/')
            continue
        for rel, entry in tree(checkout / top).items():
            parts = PurePosixPath(rel).parts
            if '__pycache__' in parts:
                if entry[0] in ('link', 'other'):
                    problems.append(f'link or special file in pycache: {top}/{rel}')
                continue
            if entry[0] == 'file':
                seen[f'{top}/{rel}'] = entry[1]
            elif entry[0] != 'dir':
                problems.append(f'link or special file in tool code: {top}/{rel}')
    for rel in sorted(set(seen) | set(pinned)):
        if rel not in seen:
            problems.append(f'pinned file missing: {rel}')
        elif rel not in pinned:
            problems.append(f'unpinned file in tool code: {rel}')
        elif seen[rel] != pinned[rel]:
            problems.append(f'tool file differs from pin: {rel}')
    return problems


def verify_state(checkout, state, backend='builtin', pinned=None, mounted_at=None):
    """Verify the owned engine using the qualified tool's state-manifest reader.

    Called only after verify_code succeeds. Synthetic adapter-policy fixtures
    supply their own code map without an engine; production PINNED_CODE always
    includes it. No routing, installation or network is performed here.

    The pinned tool binds a state manifest to the directory it describes
    (`state_dir`) and refuses to start anywhere else. mounted_at is where the
    routing sandbox presents this state (SANDBOX['checkout'] by default): a
    manifest recorded for any other directory would only fail inside the
    sandbox, so it is refused here first. The remaining checks read the files
    at `state`, wherever the caller sees them.

    Both layouts the pin installs qualify, and only when built from the pinned
    source: the legacy one (no generation; engine in the checkout, venv at
    .venv-krt, as the image's install.sh builds it) and a router generation
    (`kicad-route update`; STATE/.router-generations/<hex>/ holds a copy of the
    source and its venv). A generation's digest is compared with the checkout's
    SOURCE_ROOT, never with itself, so a stale generation is refused.
    """
    pinned = PINNED_CODE if pinned is None else pinned
    if canonical_backend(backend) != 'builtin' or 'src/kicad_route/engine.py' not in pinned:
        return []
    mounted_at = SANDBOX['checkout'] if mounted_at is None else str(mounted_at)
    script = r'''
import json, re, sys
from pathlib import Path
checkout, state, mounted_at = map(Path, sys.argv[1:4])
GENERATION = re.compile(sys.argv[4])
sys.path.insert(0, str(checkout / 'src'))
from kicad_route import common, engine, statemanifest
# statemanifest.read() also requires state_dir to equal the path this process
# sees, which differs between the host and the sandbox; mounted_at is checked instead.
m = common.load_json(statemanifest.manifest_path(state))
if not isinstance(m, dict):
    raise ValueError('missing or unreadable state manifest')
problems = statemanifest.schema_problems(m)
if m.get('state_dir') != str(mounted_at):
    problems.append(f"state manifest describes {m.get('state_dir')!r}, not the sandbox state "
                    f"{str(mounted_at)!r}; record it with `kicad-route state-manifest` where "
                    f"KICAD_ROUTE_STATE is {str(mounted_at)!r}")
k = m.get('krt') or {}
# The installed engine must be built from the pinned source in the verified checkout.
# Under a router generation both the manifest and the pin's engine.ROOT describe the
# generation's own copy, so the reference is always the checkout's SOURCE_ROOT: a
# generation built from any other commit (kicad-route doctor still passes it) differs.
pinned_source = engine.source_sha256(engine.SOURCE_ROOT)
generation = k.get('generation')
if generation is not None and not (isinstance(generation, str) and GENERATION.match(generation)):
    problems.append(f'state manifest selects an invalid router generation {generation!r}')
    generation = venv_path = None
else:
    venv_path = f'{generation}/venv' if generation else '.venv-krt'
if k.get('source_sha256') != pinned_source:
    problems.append('owned engine source digest differs' + (
        f'; router generation {generation} was built from commit {k.get("commit")}, not the pinned '
        f'source {pinned_source}: rerun `kicad-route update` at the pin' if generation else ''))
module = (k.get('extension_module') or {}).get('path')
if venv_path is not None:
    venv = state / venv_path
    installed = statemanifest.extension_module(venv)
    if (not module or installed is None or
            not (state / module).resolve().is_relative_to(venv.resolve()) or
            (state / module).resolve() != installed.resolve()):
        problems.append(f'manifest must identify the installed {venv_path} grid_router extension')
if not (k.get('extension_module') or {}).get('sha256'):
    problems.append('missing installed engine digest')
if venv_path is not None and (k.get('venv') or {}).get('path') != venv_path:
    problems.append('unexpected routing environment path')
if generation:
    # The generation runs its own copy of the source: its recorded paths stay, so the
    # drift check below hashes that copy against the digest just bound to the pin.
    if (k.get('source_path') != f'{generation}/router' or
            (k.get('requirements') or {}).get('path') != f'{generation}/router/requirements.txt'):
        problems.append('router generation source or requirements path is not the generation copy')
    record = common.load_json(state / generation / 'installation.json')
    if (not isinstance(record, dict) or not isinstance(record.get('source'), dict) or
            record['source'].get('source_sha256') != pinned_source or
            record.get('extension_sha256') != (k.get('extension_module') or {}).get('sha256')):
        problems.append('router generation installation record differs from the pinned source or module')
elif k:
    # The manifest was installed on the host. The immutable source is mounted at a
    # different absolute path inside the sandbox, and a separate state holds no
    # source at all; verify the recorded source hashes on the mounted checkout.
    k['requirements']['path'] = str(engine.SOURCE_ROOT / 'requirements.txt')
    k['source_path'] = str(engine.SOURCE_ROOT)
problems += [f"state manifest drift: {d['item']}" for d in statemanifest.verify(m, state)]
print(json.dumps(problems))
'''
    try:
        # -B still reads caches. /dev/null cannot hold ignored bytecode.
        result = subprocess.run([PYTHON, '-I', '-B', '-X', 'pycache_prefix=/dev/null',
                                 '-c', script, str(checkout), str(state), mounted_at, GENERATION.pattern],
                                capture_output=True, text=True, timeout=60,
                                env={'PATH': '/usr/bin:/bin', 'PYTHONDONTWRITEBYTECODE': '1'})
        if result.returncode:
            return ['installed engine verification failed: ' + result.stderr.strip()[-2000:]]
        problems = json.loads(result.stdout)
        if not isinstance(problems, list) or any(not isinstance(p, str) for p in problems):
            return ['invalid installed engine verification output']
        return problems
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        return [f'installed engine verification unavailable: {exc}']


def _safe(value, label, suffix=None):
    if not isinstance(value, str) or not SAFE.match(value) or (suffix and not value.endswith(suffix)):
        raise RequestError(f'{label} must match {SAFE.pattern}' + (f' and end with {suffix}' if suffix else ''))
    return value


def _bounded(value, label, low, high):
    if not re.fullmatch(r'[0-9]{1,6}', value or '') or not low <= int(value) <= high:
        raise RequestError(f'{label} must be an integer from {low} to {high}')
    return int(value)


def parse_request(argv):
    """The coordinator-declared request. No argparse: its usage exit 2 would read as partial."""
    known = {'--project', '--board', '--schematic', '--name', '--label', '--budget', '--timeout',
             '--backend', '--state', '--diff-pairs', '--layer-policy', '--experiment-policy'}
    if len(argv) % 2:
        raise RequestError('arguments must be --option value pairs')
    raw = {}
    for flag, value in zip(argv[::2], argv[1::2]):
        if flag not in known or flag in raw:
            raise RequestError(f'unknown or repeated option {flag!r}')
        raw[flag] = value
    missing = sorted({'--project', '--board', '--schematic', '--name', '--label', '--budget', '--timeout'} - set(raw))
    if missing:
        raise RequestError(f'missing options: {", ".join(missing)}')
    project = raw['--project']
    parts = PurePosixPath(project).parts if isinstance(project, str) else ()
    if not parts or str(PurePosixPath(project)) != project or any(not SAFE.match(p) for p in parts):
        raise RequestError('project must be a normalized relative candidate directory')
    request = {
        'project': project,
        'board': _safe(raw['--board'], 'board', '.kicad_pcb'),
        'schematic': _safe(raw['--schematic'], 'schematic', '.kicad_sch'),
        'name': _safe(raw['--name'], 'name'),
        'label': _safe(raw['--label'], 'label'),
        'budget_s': _bounded(raw['--budget'], 'budget', 1, 86400),
        'timeout_s': _bounded(raw['--timeout'], 'timeout', 1, 86400),
        'backend': raw.get('--backend', 'builtin'),
        'state': raw.get('--state', 'checkout'),
    }
    if request['backend'] not in BACKENDS:
        raise RequestError(f'backend must be one of {BACKENDS}')
    if request['state'] not in ('checkout', 'separate'):
        raise RequestError('state must be checkout or separate')
    if '--diff-pairs' in raw:
        if raw['--diff-pairs'] != 'auto' or canonical_backend(request['backend']) != 'builtin':
            raise RequestError('diff-pairs must be auto and requires the builtin (krt) backend')
        request['diff_pairs'] = 'auto'
    if '--layer-policy' in raw:
        # The pin refuses --backend-arg with --diff-pairs; freerouting has no KRT layer costs.
        if canonical_backend(request['backend']) != 'builtin' or '--diff-pairs' in raw:
            raise RequestError('layer-policy requires the builtin (krt) backend without diff-pairs')
        request['layer_policy'] = parse_layer_policy(raw['--layer-policy'])
    if '--experiment-policy' in raw:
        request['experiment_policy'] = parse_experiment_policy(raw['--experiment-policy'])
    if request['timeout_s'] <= request['budget_s']:
        raise RequestError('timeout must exceed the router budget')
    return request


def staged_root(layout):
    """Where the adapter stages the verified input tree the router reads (AGENTEE-442)."""
    return Path(layout['work']) / 'routing' / ORIGINAL_STAGE


def staged_dir(request, layout):
    """The --dir the router is given: the staged copy of /candidate/<project>."""
    return staged_root(layout) / request['project']


def stage_original(candidate_root, inputs_tree, root):
    """Copy the verified input tree to root, a real copy with the same modes (never a
    link), and return the tree observed there. inputs_tree holds no links or special
    files: the adapter refuses those inputs before staging."""
    root.mkdir()
    for rel, entry in inputs_tree.items():
        if entry[0] == 'dir':
            (root / rel).mkdir()
        else:
            shutil.copyfile(Path(candidate_root) / rel, root / rel)
            os.chmod(root / rel, int(entry[2], 8))
    # Directory modes last: a read-only input directory still receives its files.
    for rel, entry in reversed(list(inputs_tree.items())):
        if entry[0] == 'dir':
            os.chmod(root / rel, int(entry[1], 8))
    return tree(root)


def staged_record(request, layout, staged):
    """report.inputs.staged: the staged path and the per-file manifest and tree identity
    of the copy, to equal report.inputs (the verified /candidate input)."""
    return {'path': str(staged_dir(request, layout)), 'root': str(staged_root(layout)),
            'files': files_of(staged), 'sha256': canonical_identity(staged)}


def default_layout(request):
    checkout = Path(SANDBOX['checkout'])
    state = checkout if request['state'] == 'checkout' else Path(SANDBOX['state'])
    return {'work': Path(SANDBOX['work']), 'candidate': Path(SANDBOX['candidate']), 'checkout': checkout,
            'state': state, 'pin': PIN, 'code': PINNED_CODE, 'probe': None}


def tool_argv(request, layout):
    routing = Path(layout['work']) / 'routing'
    return [PYTHON, '-B', '-X', f'pycache_prefix={routing / "pycache"}',
            str(Path(layout['checkout']) / 'bin' / 'kicad-route'), 'run',
            '--out', str(routing / 'out'), '--json', '--backend', PIN_BACKEND_NAMES[request['backend']],
            '--dir', str(staged_dir(request, layout)),
            '--board', request['board'], '--schematic', request['schematic'],
            '--name', request['name'], '--label', request['label'],
            '--protect', 'existing', '--budget', str(request['budget_s']),
            *(['--diff-pairs', 'auto'] if request.get('diff_pairs') == 'auto' else []),
            *policy_argv(request.get('layer_policy')),
            *(['--experiment-policy', str(routing / 'attempt' / EXPERIMENT_POLICY_FILE)]
              if 'experiment_policy' in request else [])]


def policy_argv(policy):
    if policy is None:
        return []
    return (['--pour-area-loss', repr(float(policy['pour_area_loss_pct']))] +
            [f'--backend-arg={arg}' for arg in policy_backend_args(policy)])


def tool_env(layout):
    work, routing = Path(layout['work']), Path(layout['work']) / 'routing'
    home = routing / 'home'
    return {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8',
            'TMPDIR': str(routing / 'tmp'), 'XDG_CONFIG_HOME': str(home / '.config'),
            'XDG_CACHE_HOME': str(home / '.cache'), 'XDG_DATA_HOME': str(home / '.local/share'),
            'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONPYCACHEPREFIX': str(routing / 'pycache'),
            'PYTHONNOUSERSITE': '1', 'KICAD_ROUTE_STATE': str(layout['state']),
            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_OPTIONAL_LOCKS': '0',
            'GIT_CEILING_DIRECTORIES': f'{layout["candidate"]}:{staged_root(layout)}:{work}'}


def expected_settings(request, rule_floor, pairs=None, reported=None):
    # The tool echoes the --backend it was given; a reported alias of the requested backend is that echo.
    backend = reported if canonical_backend(reported) == canonical_backend(request['backend']) else request['backend']
    settings = {'backend': backend, 'budget_s': request['budget_s'], 'protect': 'existing',
            'max_iterations': None, 'passes': None, 'default_clearance_mm': None,
            'clearance_margin_mm': None, 'pour_area_loss_pct': None, 'pour_min_spokes': None,
            'backend_args': [], 'backend_env': {}, 'reserve_spokes': None, 'barrier': None, 'stall_s': None,
            'mode': 'all', 'rule_floor_sha256': rule_floor}
    if request.get('diff_pairs') == 'auto':
        settings.update(diff_pairs='auto', pairs=pairs)
    if request.get('layer_policy') is not None:
        policy = request['layer_policy']
        settings.update(pour_area_loss_pct=float(policy['pour_area_loss_pct']),
                        backend_args=policy_backend_args(policy))
    if request.get('experiment_policy') is not None:
        # KICADROUTE-91 (a0ed434 onward) records the declaration it loaded in the run
        # settings as common.sha256_of(policy): a different policy is a different attempt.
        settings['experiment_policy_sha256'] = experiment_policy_identity(request['experiment_policy'])['policy_sha256']
    return settings


def write_probe(targets):
    """Try to create a file in each directory. Each must be refused by the OS."""
    records = []
    for target in targets:
        path = Path(target) / f'.agent-ee-write-probe-{os.getpid()}-{secrets.token_hex(8)}'
        record = {'directory': str(target)}
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        except OSError as exc:
            record.update(refused=True, errno=errno.errorcode.get(exc.errno, str(exc.errno)))
        else:
            os.close(fd)
            os.unlink(path)  # our own probe file; the state must not keep it
            record.update(refused=False, errno=None)
        records.append(record)
    return records


def missing_reports(retained):
    """EXPECTED_REPORTS absent from retained, whose keys are relative to the attempt directory."""
    return [n for n in EXPECTED_REPORTS if not any(k == n or k.startswith(n + '/') for k in retained)]


def fingerprint(files):
    """The pinned tool's input fingerprint over a reference snapshot's file hashes."""
    return hashlib.sha256(''.join(f'{n}\0{d}\n' for n, d in sorted(files.items())).encode()).hexdigest()


def backend_of(result):
    """Backend identity copied verbatim from a parsed result.json; nothing is filled in."""
    if isinstance(result, dict) and isinstance(result.get('backend'), dict):
        return {'status': 'reported', 'value': result['backend'], 'reason': None}
    return dict(NO_BACKEND)


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def recomputed_exit(result):
    if result['process']['status'] != 'PASS':
        return 1
    if result['validation']['status'] != 'PASS':
        return 3
    if result['routing']['status'] != 'COMPLETE':
        return 2
    return 0


def validated_candidate(validation, sha):
    """Bind the post-refill candidate to DRC while recording the pre-refill hash."""
    delivered = validation.get('delivered_board_sha256')
    return (isinstance(delivered, str) and re.fullmatch(r'[0-9a-f]{64}', delivered) is not None and
            validation.get('candidate_board_sha256') == sha and
            validation.get('validated_board_sha256') == sha and
            validation.get('refill_modified_board') is (delivered != sha))


def refused_output(stdout):
    """Only a known machine refusal can classify exit 4 as a refusal."""
    try:
        record = json.loads(stdout)
    except (ValueError, RecursionError):
        # ValueError includes an integer beyond the interpreter's digit limit.
        return False
    return (isinstance(record, dict) and record.get('schema') == 'kicad-route/refusal/1' and
            record.get('kind') == 'refusal' and _is_int(record.get('exit_code')) and
            record['exit_code'] == 4 and record.get('command') == 'run' and
            record.get('reason_class') in ('usage', 'input', 'ledger', 'canary', 'state') and
            isinstance(record.get('message'), str) and bool(record['message']))


def semantics(exit_code, result, attempt, refused_line, discovered, request):
    """Classify one finished attempt from its observed exit and reports alone.

    exit_code is the process exit; result/attempt are the parsed JSON objects
    (None when absent); discovered says whether exactly one attempt directory
    exists. The numeric exit is never trusted alone: each exit needs its exact
    status combination, and anything else is a contradiction."""
    reasons = []
    if not _is_int(exit_code):
        return 'contradiction', ['router did not exit normally']
    if result is None:
        if exit_code == 4 and not discovered and refused_line:
            return 'refused', ['refused before routing; the router removed its run directory']
        if exit_code == 1:
            return 'backend_failure', ['router failed before writing result.json']
        if exit_code == 2:
            return 'contradiction', ['exit 2 without result.json is a usage error, never a partial route']
        return 'contradiction', [f'exit {exit_code} without result.json']
    if exit_code == 4:
        return 'contradiction', ['refusal exit with a result.json']
    paired = request.get('diff_pairs') == 'auto'
    version = 2 if paired else 1
    if isinstance(result, dict) and result.get('schema') != f'kicad-route/route/{version}':
        return 'contradiction', ['unknown result schema version']
    custody = (canonical_backend(request['backend']) == 'builtin' and isinstance(result, dict) and
               isinstance(result.get('process'), dict) and result['process'].get('status') == 'PASS')
    # A K91 experiment reference is optional; experiment_grade retains or refuses it.
    if (not isinstance(result, dict) or
            set(result) - {'experiment'} !=
            RESULT_KEYS | ({'pairs'} if paired else set()) | ({'copper_ownership'} if custody else set())):
        return 'contradiction', ['result.json shape differs from the pinned tool']
    if custody and not copper_ownership_shape(result['copper_ownership']):
        return 'contradiction', ['copper_ownership differs from the pinned tool']
    if not measurement_shape(result):
        return 'contradiction', ['process.measurement_qualification differs from the pinned tool']
    if canonical_backend(request['backend']) == 'builtin' and not runtime_shape(result.get('backend')):
        return 'contradiction', ['backend.runtime differs from the pinned tool']
    if (canonical_backend(request['backend']) != 'builtin' and isinstance(result.get('backend'), dict) and
            'runtime' in result['backend']):
        return 'contradiction', ['backend.runtime is reported by a backend that has none']
    if result['kind'] != 'route' or not _is_int(result['exit_code']) or result['exit_code'] != exit_code:
        return 'contradiction', ['result.json kind or exit_code contradicts the process exit']
    statuses = [(result.get(k), k, allowed) for k, allowed in
                (('process', PROCESS), ('routing', ROUTING), ('validation', VALIDATION))]
    for value, key, allowed in statuses:
        if not isinstance(value, dict) or value.get('status') not in allowed:
            return 'contradiction', [f'unknown {key} status']
    if recomputed_exit(result) != exit_code:
        return 'contradiction', ['statuses imply a different exit than the one observed']
    backend = result['backend']
    if not isinstance(backend, dict) or canonical_backend(backend.get('name')) != canonical_backend(request['backend']):
        return 'contradiction', ['result backend differs from the requested backend']
    inp = result['input']
    if not isinstance(inp, dict) or inp.get('board') != request['board'] or inp.get('project') != request['name']:
        return 'contradiction', ['result input differs from the requested board or name']
    settings = result['settings']
    floor = settings.get('rule_floor_sha256') if isinstance(settings, dict) else None
    pairs = settings.get('pairs') if isinstance(settings, dict) and paired else None
    reported = settings.get('backend') if isinstance(settings, dict) else None
    if settings != expected_settings(request, floor, pairs, reported) or not (floor is None or isinstance(floor, str)):
        return 'contradiction', ['result settings differ from the fixed adapter settings']
    if not discovered:
        return 'contradiction', ['result.json without exactly one attempt directory']
    declared = request.get('experiment_policy')
    if not isinstance(attempt, dict) or set(attempt) != ATTEMPT_KEYS | ({'pairs', 'pair_routing'} if paired else set()) | (
            {'experiment_policy'} if declared is not None else set()):
        return 'contradiction', ['attempt.json missing or shaped differently from the pinned tool']
    # KICADROUTE-91 (a0ed434 onward) records the declaration it loaded in attempt.json.
    if declared is not None:
        try:
            recorded = canonical_json(attempt['experiment_policy'])
        except (TypeError, ValueError):
            recorded = None
        if recorded != canonical_json(declared):
            return 'contradiction', ['attempt.json experiment_policy differs from the declared policy']
    if attempt['schema'] != f'kicad-route/attempt/{version}':
        return 'contradiction', ['unknown attempt schema version']
    if paired and not pair_binding(pairs, attempt, result):
        return 'contradiction', ['pair evidence differs from the declared pair plan']
    dirname = PurePosixPath(request['project']).name
    if (attempt['board'] != request['board'] or attempt['schematic'] != request['schematic'] or
            canonical_backend(attempt['backend']) != canonical_backend(request['backend']) or attempt['project'] != request['name'] or
            attempt['reference_dir'] != f'reference/{dirname}' or
            attempt['candidate_dir'] != f'candidate/{dirname}' or
            attempt['input_fingerprint'] != inp.get('fingerprint') or attempt['settings'] != settings):
        return 'contradiction', ['attempt.json contradicts result.json or the request']
    process, routing, validation = (result[k]['status'] for k in ('process', 'routing', 'validation'))
    candidate = result['candidate']
    if process == 'FAIL':
        if routing != 'NOT_RUN' or validation != 'NOT_RUN' or candidate is not None:
            return 'contradiction', ['process failure with a candidate or graded statuses']
        return 'backend_failure', list(result['process'].get('reasons') or ['process FAIL'])
    if not isinstance(candidate, dict) or set(candidate) != {'dir', 'board'}:
        return 'contradiction', ['process PASS without a candidate record']
    if validation != 'PASS':
        reasons = result['validation'].get('reasons')
        return 'validation_failed', list(reasons) if isinstance(reasons, list) else ['validation FAIL']
    if routing == 'COMPLETE':
        return 'complete', reasons
    if routing == 'INCOMPLETE':
        return 'incomplete', [f"{result['routing'].get('unconnected_in_scope')} unconnected in scope"]
    return 'contradiction', ['validation PASS with routing NOT_RUN']


def terminal_problems(result):
    """An eligible attempt must carry the pin's native terminal preservation (KICADROUTE-98).

    Every originally joined pad group, including subsets of incomplete nets, must stay
    joined; the pin reports that as validation.net_completion from native terminal
    partitions, not from the capped DRC list. Missing or failing evidence, a split group,
    or a COMPLETE route whose native terminals are still split is never eligible."""
    validation = result.get('validation') if isinstance(result, dict) else None
    completion = validation.get('net_completion') if isinstance(validation, dict) else None
    native = validation.get('native_terminal_evidence') if isinstance(validation, dict) else None
    if (not isinstance(completion, dict) or completion.get('status') != 'PASS' or
            completion.get('split_components') != [] or completion.get('nets_lost') != []):
        return ['native terminal preservation (net_completion) is missing or did not pass']
    metrics = native.get('metrics') if isinstance(native, dict) else None
    candidate = metrics.get('candidate') if isinstance(metrics, dict) else None
    if (not isinstance(candidate, dict) or not isinstance(metrics.get('reference'), dict) or
            any(not _is_int(candidate.get(k)) or candidate[k] < 0
                for k in ('terminal_nets', 'incomplete_nets', 'component_deficit')) or
            any(not isinstance(native.get(k), str) or not native[k] for k in ('reference', 'candidate'))):
        return ['native terminal evidence is missing or malformed']
    if result['routing'].get('status') == 'COMPLETE' and (candidate['incomplete_nets'] or candidate['component_deficit']):
        return ['routing COMPLETE while native terminals remain split']
    return []


def measurement_shape(result):
    """process.measurement_qualification as the pin writes it, agreeing with the top-level measurement."""
    process = result.get('process')
    value = process.get('measurement_qualification') if isinstance(process, dict) else None
    return (isinstance(value, dict) and set(value) == MEASUREMENT_QUALIFICATION and
            isinstance(value['measurement'], bool) and value['measurement'] is result.get('measurement') and
            (value['build_reproducible'] is None or type(value['build_reproducible']) is bool) and isinstance(value['reasons'], list) and
            all(isinstance(r, str) and r for r in value['reasons']))


def runtime_shape(backend):
    """backend.runtime of an owned-engine result: exactly source, venv and generation paths."""
    runtime = backend.get('runtime') if isinstance(backend, dict) else None
    return (isinstance(runtime, dict) and set(runtime) == RUNTIME_KEYS and
            all(isinstance(runtime[k], str) and runtime[k].startswith('/') for k in ('source', 'venv')) and
            (runtime['generation'] is None or
             (isinstance(runtime['generation'], str) and runtime['generation'].startswith('/'))))


def runtime_problems(request, layout, generation, result):
    """The engine an owned-engine attempt reports it ran must be the one verified before it ran."""
    if canonical_backend(request['backend']) != 'builtin' or not isinstance(result, dict):
        return []
    runtime = (result.get('backend') or {}).get('runtime') if isinstance(result.get('backend'), dict) else None
    if runtime != runtime_paths(layout, generation):
        return ['the router ran an engine other than the verified '
                + (f'router generation {generation}' if generation else 'checkout engine')]
    return []


def copper_ownership_shape(value):
    """The pin's copper-custody pointer: bound to one retained record, or unavailable with reasons."""
    if not isinstance(value, dict) or value.get('replay_authority') is not False:
        return False
    if value.get('status') == 'bound':
        return (set(value) == COPPER_OWNERSHIP_BOUND and value['record'] == COPPER_OWNERSHIP_RECORD and
                isinstance(value['sha256'], str) and re.fullmatch(r'[0-9a-f]{64}', value['sha256']) is not None and
                value['coverage'] == 'current_run_only_input_fixed')
    return (value.get('status') == 'unavailable' and set(value) == COPPER_OWNERSHIP_UNAVAILABLE and
            isinstance(value['reasons'], list) and bool(value['reasons']) and
            all(isinstance(r, str) and r for r in value['reasons']))


def pair_binding(plans, record, result):
    """Bind opt-in pair reports to the same plan; native geometry is not graded here."""
    keys = {'p', 'n', 'netclass', 'width_mm', 'gap_mm'}
    if not isinstance(plans, list) or not isinstance(record['pairs'], list):
        return False
    if len(plans) != len(record['pairs']) or not isinstance(record['pair_routing'], dict):
        return False
    members = set()
    for plan, pair in zip(plans, record['pairs']):
        if not isinstance(plan, dict) or set(plan) != keys or not isinstance(pair, dict):
            return False
        if set(pair) not in (keys, keys | {'initially_connected'}):
            return False
        if {k: pair[k] for k in keys} != plan:
            return False
        if 'initially_connected' in pair and not isinstance(pair['initially_connected'], bool):
            return False
        if any(not isinstance(plan[k], str) or not plan[k] for k in ('p', 'n', 'netclass')):
            return False
        if plan['p'] == plan['n'] or members & {plan['p'], plan['n']}:
            return False
        members.update((plan['p'], plan['n']))
        if any(type(plan[k]) not in (int, float) or not 0 < plan[k] < float('inf')
               for k in ('width_mm', 'gap_mm')):
            return False
    if not set(record['pair_routing']) <= {p['p'] for p in plans}:
        return False
    reports = result['pairs']
    if not isinstance(reports, list) or len(reports) != len(plans):
        return False
    for plan, report in zip(plans, reports):
        if not isinstance(report, dict) or any(report.get(k) != v for k, v in plan.items()):
            return False
    return True


def pair_coverage(request, result, record, classification):
    """Describe retained pair evidence, never replace fresh board acceptance checks.

    In particular routing completeness cannot imply coupled routing, and preserved
    input connectivity cannot claim this invocation created a differential pair.
    """
    limitations = ['Pair reports do not qualify length/skew limits or impedance; fresh board checks are required.']
    coverage = {'status': 'unknown', 'pairs': [], 'limitations': limitations, 'acceptance_ready': False}
    if request.get('diff_pairs') != 'auto':
        scope = record.get('scope', {}) if isinstance(record, dict) else {}
        nets = scope.get('nets', []) if isinstance(scope, dict) else []
        if (isinstance(nets, list) and all(isinstance(n, str) for n in nets) and
                any(n.rsplit('/', 1)[-1] == 'CAN+' and n[:-1] + '-' in nets for n in nets)):
            completed = (classification in ('complete', 'incomplete') or
                         (classification == 'validation_failed' and isinstance(result, dict) and
                          result.get('routing', {}).get('status') == 'COMPLETE' and
                          result.get('process', {}).get('status') == 'PASS'))
            limitations.append('CAN pair routed single-ended' if completed else
                                'CAN pair coupling unavailable; single-ended attempt did not complete routing')
            if classification not in ('complete', 'incomplete'):
                limitations.append('Single-ended attempt did not produce an eligible board')
        else:
            limitations.append('Differential pair routing was not requested; pair geometry is unqualified')
        return coverage
    if (classification not in ('complete', 'incomplete') or not isinstance(result, dict) or
            not isinstance(record, dict) or not pair_binding(result.get('settings', {}).get('pairs'), record, result)):
        limitations.append('Requested differential pair capability or eligible native pair evidence is unavailable')
        return coverage
    reports = result['pairs']
    if not reports:
        limitations.append('No differential pairs were detected; no pair geometry is qualified')
        return coverage
    for pair in reports:
        measured = all(type(pair.get(k)) in (int, float) and 0 <= pair[k] < float('inf')
                       for k in ('length_p', 'length_n', 'skew'))
        measured = measured and abs(abs(pair['length_p'] - pair['length_n']) - pair['skew']) <= .000002
        coupled = (pair.get('backend_status') == 'COUPLED' and pair.get('status') == 'ROUTED' and
                   pair.get('routed') is True and type(pair.get('gap_violations')) is int and
                   pair['gap_violations'] == 0 and measured)
        coverage['pairs'].append({'p': pair['p'], 'n': pair['n'],
            'status': 'reported_coupled' if coupled else 'unknown'})
        if not coupled:
            limitations.append(f"{pair['p']}/{pair['n']}: coupled geometry is unqualified "
                               f"(backend status {pair.get('backend_status')})")
    if all(p['status'] == 'reported_coupled' for p in coverage['pairs']):
        coverage['status'] = 'reported_coupled'
    return coverage


# Run with KiCad's Python on the graded board: the reference net's native fill on
# each reference layer must cover every RF track. Only the clearance the zone
# filler cut for the RF net's own pads is excused, rebuilt with the filler's
# geometry (rule clearance plus its extra clearance, at the board's arc error);
# any other uncovered area interrupts the plane. An RF via through a reference
# layer interrupts it by its antipad. Nothing is filled or saved.
MEASURE_REFERENCE = r"""
import hashlib, json, re, sys
from pathlib import Path
import pcbnew as k
board_path, spec, out_path = sys.argv[1], json.loads(sys.argv[2]), sys.argv[3]
sha = hashlib.sha256(open(board_path, 'rb').read()).hexdigest()
b = k.LoadBoard(board_path)
ds = b.GetDesignSettings()
layers = {b.GetLayerName(l): l for l in b.GetEnabledLayers().CuStack()}
err, filler_err = k.FromMM(0.001), ds.m_MaxError
extra = k.FromMM(spec['extra_clearance_mm'])
problems, tracks, vias = [], [], []
for rules in sorted(Path(board_path).parent.glob('*.kicad_dru')):
    if re.search(spec['clearance_rule'], rules.read_text(errors='replace')):
        problems.append('custom clearance rules in ' + rules.name + ': the plane clearance is not evaluable')
names = {str(n) for n in b.GetNetsByName().keys()}
for net in [spec['net'], *spec['rf_nets']]:
    if net not in names:
        problems.append('net absent from the board: ' + net)
fills, gaps = {}, {}
for name in spec['layers']:
    if name not in layers:
        problems.append('reference layer not on the board: ' + name)
        continue
    layer, fill, gap = layers[name], k.SHAPE_POLY_SET(), None
    for z in b.Zones():
        if z.GetIsRuleArea() or z.GetNetname() != spec['net'] or not z.IsOnLayer(layer):
            continue
        if not z.IsFilled():
            problems.append('reference zone is not filled on ' + name)
        fill.BooleanAdd(z.GetFilledPolysList(layer))
        # The smallest zone clearance: a larger one would excuse more than one zone cut.
        own = z.GetOwnClearance(layer)
        gap = own if gap is None else min(gap, own)
    if gap is None:
        problems.append('no reference zone on ' + name)
    fills[name], gaps[name] = fill, gap or 0
pads = [p for fp in b.GetFootprints() for p in fp.Pads() if p.GetNetname() in spec['rf_nets']]
keeps = {}
for name, layer in ((n, layers[n]) for n in fills):
    keep = k.SHAPE_POLY_SET()
    for p in pads:
        clear = max(gaps[name], p.GetOwnClearance(layer), ds.m_MinClearance)
        if p.FlashLayer(layer):
            p.TransformShapeToPolygon(keep, layer, clear + extra, filler_err, k.ERROR_OUTSIDE)
        if p.HasHole():
            # The lesser of the two clearances a hole may carry: never more than KiCad cut.
            hole = min(clear, ds.m_HoleClearance)
            p.TransformHoleToPolygon(keep, hole + extra, filler_err, k.ERROR_OUTSIDE)
    keeps[name] = keep
for t in b.GetTracks():
    if t.GetNetname() not in spec['rf_nets']:
        continue
    if t.GetClass() == 'PCB_VIA':
        for name in spec['layers']:
            if name in layers and t.IsOnLayer(layers[name]):
                vias.append({'uuid': t.m_Uuid.AsString(), 'net': t.GetNetname(), 'kind': 'PCB_VIA',
                             'layers': [b.GetLayerName(t.TopLayer()), b.GetLayerName(t.BottomLayer())],
                             'reference_layer': name, 'status': 'interrupted',
                             'reason': 'RF via antipad through the reference plane'})
        continue
    if t.GetClass() not in ('PCB_TRACK', 'PCB_ARC'):
        continue
    for name in spec['layers']:
        rec = {'uuid': t.m_Uuid.AsString(), 'net': t.GetNetname(), 'kind': t.GetClass(),
               'layer': b.GetLayerName(t.GetLayer()), 'reference_layer': name}
        if name not in fills:
            rec.update(status='unmeasured', reason='reference layer not on the board')
        elif rec['layer'] == name:
            rec.update(status='unmeasured', reason='RF track on its own reference layer')
        else:
            shape = k.SHAPE_POLY_SET()
            t.TransformShapeToPolygon(shape, t.GetLayer(), 0, err, k.ERROR_INSIDE)
            shape.BooleanSubtract(fills[name])
            shape.BooleanSubtract(keeps[name])
            area = shape.Area() / 1e12
            rec.update(uncovered_mm2=round(area, 6),
                       status='interrupted' if area > spec['tolerance_mm2'] else 'continuous')
        tracks.append(rec)
json.dump({'schema': 'agent-ee.reference-continuity/2', 'board_sha256': sha, 'kicad': k.Version(),
           'scope': spec['scope'], 'spec': spec, 'problems': problems, 'tracks': tracks, 'vias': vias},
          open(out_path, 'w'), indent=1, sort_keys=True)
"""


def reference_spec(policy):
    return {**policy['reference'], 'extra_clearance_mm': FILLER_EXTRA_CLEARANCE_MM,
            'tolerance_mm2': CONTINUITY_TOLERANCE_MM2, 'clearance_rule': CLEARANCE_RULE.pattern,
            'scope': CONTINUITY_SCOPE}


def measure_reference(policy, board, routing, env):
    """KiCad's measurement of the declared reference plane under the RF nets, retained
    beside the router's reports. A failed measurement leaves no file: unknown."""
    out = routing / 'attempt' / POLICY_FILE
    with open(out.with_suffix('.log'), 'wb') as log:
        try:
            subprocess.run([PYTHON, '-I', '-B', '-c', MEASURE_REFERENCE, str(board),
                            canonical_json(reference_spec(policy)), str(out)],
                           cwd=routing, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                           timeout=600, check=False)
        except subprocess.TimeoutExpired:
            log.write(b'reference measurement timed out\n')
    if out.is_file() and not out.is_symlink():
        return digest(out)
    return None


def _item(status, **detail):
    return {'status': status, **detail}


def layer_policy_grade(policy, attempt_dir, result, classification, board):
    """Grade a declared layer policy from the retained reports alone.

    Each item is pass, defect or unknown; anything unmeasured is unknown and the
    whole grade is pass only when every item passes."""
    if classification not in ('complete', 'incomplete', 'validation_failed', 'policy_failed'):
        return {'status': 'unknown', 'items': {}, 'reason': f'{classification}: no routed board was graded'}
    attempt_dir = Path(attempt_dir)

    def read(rel):
        path = attempt_dir / rel
        return load_json(path, [], rel) if path.is_file() and not path.is_symlink() else None

    validation = result.get('validation') if isinstance(result, dict) else None
    sha = validation.get('validated_board_sha256') if isinstance(validation, dict) else None
    if not isinstance(sha, str) or not re.fullmatch(r'[0-9a-f]{64}', sha):
        return {'status': 'unknown', 'items': {}, 'reason': 'no validated board hash'}
    items = {}
    allowed = set(policy['signal_layers'])
    cand, ref = read('validate/candidate-facts.json'), read('facts/reference-facts.json')
    if (not isinstance(cand, dict) or not isinstance(cand.get('tracks'), list) or
            not isinstance(ref, dict) or not isinstance(ref.get('tracks'), list)):
        items['disallowed_tracks'] = _item('unknown', reason='candidate or reference board facts unavailable')
    elif cand.get('sha256') != sha:
        items['disallowed_tracks'] = _item('unknown', reason='candidate facts are not of the validated board')
    elif not all(isinstance(t, dict) and isinstance(t.get('uuid'), str) and isinstance(t.get('layer'), str)
                 for t in cand['tracks'] + ref['tracks']):
        items['disallowed_tracks'] = _item('unknown', reason='malformed track facts')
    else:
        items['disallowed_tracks'] = disallowed_tracks(allowed, cand, ref, read('snapshot.json'), board)
    checks = read('validate/preservation.json')
    pour = [c for c in checks if isinstance(c, dict) and c.get('check') == 'pour_integrity'] \
        if isinstance(checks, list) else []
    if len(pour) != 1 or pour[0].get('status') not in ('PASS', 'FAIL'):
        items['pour_integrity'] = _item('unknown', reason='pour_integrity was not graded')
    else:
        items['pour_integrity'] = _item('pass' if pour[0]['status'] == 'PASS' else 'defect',
                                        detail=pour[0].get('detail'))
    delta, bound = read('validate/pour-integrity.json'), float(policy['pour_area_loss_pct'])
    layers = delta.get('layers') if isinstance(delta, dict) else None
    if not isinstance(layers, list) or not all(
            isinstance(r, dict) and isinstance(r.get('net_layer'), str) and
            (r.get('area_delta_pct') is None or type(r.get('area_delta_pct')) in (int, float)) for r in layers):
        items['area_loss'] = _item('unknown', reason='pour area deltas unavailable')
    elif delta.get('area_loss_limit_pct') != bound:
        items['area_loss'] = _item('unknown', reason='the router graded a different area-loss bound')
    elif not layers:
        items['area_loss'] = _item('unknown', reason='no copper pour was measured')
    else:
        items['area_loss'] = area_loss(policy, layers, bound)
    if 'reference' in policy:
        items['reference_continuity'] = reference_grade(policy, read(POLICY_FILE), sha, classification)
    if 'per_net_signal_layers' in policy:
        items['per_net_signal_layers'] = per_net_layer_grade(policy, cand, sha)
    statuses = {i['status'] for i in items.values()}
    status = 'defect' if 'defect' in statuses else 'unknown' if 'unknown' in statuses else 'pass'
    return {'status': status, 'items': items}


def per_net_layer_grade(policy, facts, sha):
    """All routed copper of declared nets, including input copper and full via spans.

    Reuse the pinned native board inventory; no pre-existing-copper exemption.
    An absent declaration is unknown, never implied by the global allowlist. Every
    graded item names the validated board hash it is evidence for (board_sha256)
    and the declaration it graded, so a consumer binds it to one candidate.
    """
    declared = policy.get('per_net_signal_layers')
    if declared is None:
        return _item('unknown', board_sha256=sha, reason='per-net signal layers were not declared')

    def item(status, **detail):
        return _item(status, board_sha256=sha, declared=declared, **detail)

    if not isinstance(facts, dict) or facts.get('sha256') != sha:
        return item('unknown', reason='native facts are not of the validated board')
    copper = facts.get('copper_layers')
    if (not isinstance(copper, list) or not all(isinstance(c, dict) and
            isinstance(c.get('name'), str) and COPPER_LAYER.fullmatch(c['name']) for c in copper)):
        return item('unknown', reason='native copper stack unavailable')
    stack = [c['name'] for c in copper]
    expected = ['F.Cu', *sorted((n for n in stack if n.startswith('In')),
                               key=lambda n: int(n[2:-3])), 'B.Cu']
    if len(stack) != len(set(stack)) or stack != expected:
        return item('unknown', reason='native copper stack malformed')
    tracks, vias = facts.get('tracks'), facts.get('vias')
    if not isinstance(tracks, list) or not isinstance(vias, list):
        return item('unknown', reason='native track or via inventory unavailable')
    problems, violations, measured = [], [], {net: 0 for net in declared}
    for net, allowed in declared.items():
        if not set(allowed) <= set(stack):
            problems.append(f'{net}: declared layer absent from board')

    def point(value):
        return isinstance(value, list) and len(value) == 2 and all(_is_int(n) for n in value)

    for kind, records in (('track', tracks), ('via', vias)):
        for rec in records:
            if not isinstance(rec, dict) or not isinstance(rec.get('net'), str):
                problems.append('unparseable native net identity')
                continue
            net = rec['net']
            if net not in declared:
                continue
            if not isinstance(rec.get('uuid'), str) or not rec['uuid']:
                problems.append(f'{net}: missing copper UUID')
                continue
            if kind == 'track':
                layers = [rec.get('layer')]
                valid = (rec.get('kind') in ('segment', 'arc') and layers[0] in stack and
                         _is_int(rec.get('width')) and rec['width'] > 0 and
                         point(rec.get('start')) and point(rec.get('end')) and
                         (rec['kind'] != 'arc' or point(rec.get('mid'))))
            else:
                pair = rec.get('layers')
                valid = (isinstance(pair, list) and len(pair) == 2 and all(n in stack for n in pair) and
                         pair[0] != pair[1] and point(rec.get('position')) and
                         rec.get('via_type') in ('through', 'blind', 'buried', 'micro') and
                         _is_int(rec.get('diameter')) and _is_int(rec.get('drill')) and
                         0 < rec['drill'] < rec['diameter'])
                layers = []
                if valid:
                    start, end = sorted(stack.index(n) for n in pair)
                    layers = stack[start:end + 1]
                    if rec['via_type'] == 'through' and layers != stack:
                        valid = False
            if not valid:
                problems.append(f'{net}: unparseable {kind} geometry ({rec["uuid"]})')
                continue
            measured[net] += 1
            disallowed = [n for n in layers if n not in declared[net]]
            if disallowed:
                violations.append({'uuid': rec['uuid'], 'net': net, 'kind': kind,
                                   'layers': layers, 'disallowed_layers': disallowed})
    problems += [f'{net}: no routed copper measured' for net, count in measured.items() if not count]
    return item('defect' if violations else 'unknown' if problems else 'pass',
                violations=violations, problems=problems, copper_measured=measured)


def disallowed_tracks(allowed, cand, ref, snapshot, board):
    """Tracks on layers outside the allowlist. One is pre-existing only when the input
    board's own facts (bound by hash to the snapshot) hold a track of the same geometry
    that no other candidate track already matched; anything else the router added."""
    files = snapshot.get('files') if isinstance(snapshot, dict) else None
    input_sha = files.get(board) if isinstance(files, dict) else None
    if not isinstance(input_sha, str) or not re.fullmatch(r'[0-9a-f]{64}', input_sha):
        return _item('unknown', reason='no input board hash in the snapshot')
    off = sorted((t for t in cand['tracks'] if t['layer'] not in allowed),
                 key=lambda t: (t['layer'], t['uuid'], canonical_json(t)))
    show = [{'uuid': t['uuid'], 'layer': t['layer'], 'net': t.get('net'), 'kind': t.get('kind')} for t in off]
    if ref.get('sha256') != input_sha:
        return _item('defect', tracks=show, preexisting=[],
                     reason='reference facts are not of the input board: no track is exempt')
    unmatched = collections.Counter(canonical_json([t.get(k) for k in TRACK_IDENTITY]) for t in ref['tracks'])
    new, preexisting = [], []
    for track, shown in zip(off, show):
        key = canonical_json([track.get(k) for k in TRACK_IDENTITY])
        if unmatched[key] > 0:
            unmatched[key] -= 1
            preexisting.append(shown)
        else:
            new.append(shown)
    return _item('defect' if new else 'pass', tracks=new, preexisting=preexisting)


def area_loss(policy, layers, bound):
    """Pour area deltas against the bound. A delta the router could not compute (no
    initial fill) is unknown, and a defect on a declared reference plane: the plane
    the policy protects had no copper to begin with."""
    reference = {f'{policy["reference"]["net"]}@{layer}' for layer in policy['reference']['layers']} \
        if 'reference' in policy else set()
    rows = [{'net_layer': r['net_layer'], 'area_delta_pct': r['area_delta_pct'], 'area_mm2': r.get('area_mm2')}
            for r in layers]
    over = [r for r in rows if r['area_delta_pct'] is not None and -r['area_delta_pct'] > bound]
    unmeasured = [r for r in rows if r['area_delta_pct'] is None]
    empty_reference = [r for r in unmeasured if r['net_layer'] in reference]
    absent = sorted(reference - {r['net_layer'] for r in rows})
    detail = dict(limit_pct=bound, exceeded=over, unmeasured=unmeasured, layers=rows)
    if over or empty_reference:
        return _item('defect', **detail, **({'reason': 'a declared reference plane had no initial fill'}
                                            if empty_reference else {}))
    if unmeasured or absent:
        return _item('unknown', **detail, absent=absent,
                     reason='area delta not measured: no initial fill' if unmeasured else
                     'a declared reference plane was not measured by the router')
    return _item('pass', **detail)


def reference_grade(policy, measured, sha, classification):
    if classification == 'validation_failed':
        return _item('unknown', scope=CONTINUITY_SCOPE,
                     reason='the router rejected the board; continuity was not measured')
    if (not isinstance(measured, dict) or measured.get('schema') != 'agent-ee.reference-continuity/2' or
            not isinstance(measured.get('tracks'), list) or not isinstance(measured.get('vias'), list) or
            not isinstance(measured.get('problems'), list)):
        return _item('unknown', scope=CONTINUITY_SCOPE, reason='reference continuity measurement unavailable')
    if (measured.get('board_sha256') != sha or measured.get('spec') != reference_spec(policy) or
            measured.get('scope') != CONTINUITY_SCOPE):
        return _item('unknown', scope=CONTINUITY_SCOPE,
                     reason='measurement is not of the validated board and declared reference')
    tracks, vias = measured['tracks'], measured['vias']
    if (not all(isinstance(t, dict) and t.get('status') in ('continuous', 'interrupted', 'unmeasured')
                for t in tracks) or
            not all(isinstance(v, dict) and v.get('status') == 'interrupted' for v in vias)):
        return _item('unknown', scope=CONTINUITY_SCOPE, reason='malformed continuity record')
    interrupted = [t for t in tracks if t['status'] == 'interrupted']
    unmeasured = [t for t in tracks if t['status'] == 'unmeasured']
    detail = {'scope': CONTINUITY_SCOPE, 'interrupted': interrupted, 'vias': vias, 'unmeasured': unmeasured,
              'problems': measured['problems'], 'tracks_measured': len(tracks) - len(unmeasured)}
    if interrupted or vias:
        return _item('defect', **detail)
    if unmeasured or measured['problems'] or not tracks:
        return _item('unknown', **detail, reason='no RF track measured' if not tracks else 'incomplete measurement')
    return _item('pass', **detail)


def load_json(path, problems, label):
    if not path.is_file() or path.is_symlink():
        return None
    if path.stat().st_size > JSON_LIMIT:
        problems.append(f'{label} exceeds {JSON_LIMIT} bytes')
        return None
    try:
        return json.loads(path.read_bytes())
    except (UnicodeDecodeError, ValueError, RecursionError):
        # ValueError includes an integer beyond the interpreter's digit limit.
        problems.append(f'{label} is not JSON')
        return None


def discover(out, request):
    """The single regular attempt directory in a fresh private OUT, or None.

    Never follows `latest` or any path a report names; every entry under
    runs/ must be a real directory except the router's `latest` link."""
    problems = []
    runs = out / 'runs'
    if not os.path.lexists(out):
        return None, problems
    if out.is_symlink() or not out.is_dir():
        return None, ['OUT is not a directory']
    if not os.path.lexists(runs):
        return None, problems
    if runs.is_symlink() or not runs.is_dir():
        return None, ['OUT/runs is not a directory']
    found = []
    for project in sorted(runs.iterdir()):
        if project.name != request['name'] or project.is_symlink() or not project.is_dir():
            problems.append(f'unexpected entry runs/{project.name}')
            continue
        for label in sorted(project.iterdir()):
            if label.name != request['label'] or label.is_symlink() or not label.is_dir():
                problems.append(f'unexpected entry runs/{project.name}/{label.name}')
                continue
            stamps = []
            for entry in sorted(label.iterdir()):
                if entry.name == 'latest' and entry.is_symlink():
                    continue
                if entry.is_symlink() or not entry.is_dir() or not STAMP.match(entry.name):
                    problems.append(f'unexpected entry in label directory: {entry.name}')
                else:
                    stamps.append(entry)
            latest = label / 'latest'
            if latest.is_symlink() and [os.readlink(latest)] != [s.name for s in stamps]:
                problems.append('latest does not name the single attempt directory')
            found += stamps
    if len(found) > 1:
        problems.append(f'{len(found)} attempt directories in a fresh OUT')
    return (found[0] if len(found) == 1 else None), problems


def resolve_reported(value, origin, routing):
    """A report path as an untrusted string: cwd-relative or absolute, never .."""
    if not isinstance(value, str) or not value:
        return None
    path = PurePosixPath(value)
    if '..' in path.parts or '.' in path.parts:
        return None
    resolved = Path(path) if path.is_absolute() else Path(origin) / path
    return resolved if resolved.is_relative_to(routing) else None


def _no_links_between(base, path):
    current = Path(base)
    for part in Path(path).relative_to(base).parts:
        current = current / part
        if current.is_symlink():
            return False
    return True


def _project_with_sibling_links(root, project, siblings, candidate_root):
    """The names of the links beside the real project directory in root, or None
    unless root holds that directory and only the links the pinned
    copy_project/make_snapshot place for ${KIPRJMOD}/../<dir> library paths: named
    for a top-level input directory, reading literally <staged input root>/<name>.
    The links are read, never followed."""
    entries = {p.name: p for p in root.iterdir()}
    found = entries.pop(project, None)
    if found is None or found.is_symlink() or not found.is_dir():
        return None
    if not all(name in siblings and path.is_symlink() and
               os.readlink(path) == str(PurePosixPath(candidate_root) / name)
               for name, path in entries.items()):
        return None
    return set(entries)


def bind_candidate(request, layout, run_dir, result, attempt, inputs):
    """Problems binding the eligible candidate to this attempt and these inputs, the
    candidate board's sha256, the undelivered editor-state files and the whole
    candidate file inventory, which verification re-checks against the snapshot."""
    routing = Path(layout['work']) / 'routing'
    problems = []
    if resolve_reported(result['run_dir'], routing, routing) != run_dir:
        problems.append('result run_dir is not the discovered attempt directory')
    cand_dir = run_dir / attempt['candidate_dir']
    ref_dir = run_dir / attempt['reference_dir']
    board = cand_dir / request['board']
    if (resolve_reported(result['candidate']['dir'], routing, routing) != cand_dir or
            resolve_reported(result['candidate']['board'], routing, routing) != board):
        problems.append('result candidate paths are not inside the discovered attempt')
        return problems, None, {}, {}
    if not _no_links_between(run_dir, board) or not board.is_file():
        problems.append('candidate board missing, or reached through a link')
        return problems, None, {}, {}
    # Only top-level input directories: a file's path has a single part, so a
    # link named for a top-level input file stays a contradiction.
    siblings = ({PurePosixPath(rel).parts[0] for rel in inputs if len(PurePosixPath(rel).parts) > 1} -
                {PurePosixPath(request['project']).parts[0], cand_dir.name})
    links = []
    for label, root in (('candidate', run_dir / 'candidate'), ('reference', run_dir / 'reference')):
        names = None if root.is_symlink() or not root.is_dir() else _project_with_sibling_links(
            root, cand_dir.name, siblings, staged_root(layout))
        if names is None:
            problems.append(f'{label}/ must hold exactly the project directory and its sibling links')
            return problems, None, {}, {}
        links.append(names)
    # copy_project links beside the candidate each sibling the snapshot linked.
    if links[0] != links[1]:
        problems.append('candidate/ and reference/ sibling links differ')
        return problems, None, {}, {}
    cand, ref = tree(cand_dir), tree(ref_dir)
    if links_of(cand) or links_of(ref):
        problems.append('candidate or reference tree contains links or special files')
    cand_files, ref_files = files_of(cand), files_of(ref)
    inventory = dict(cand_files)
    # kicad-cli pcb drc --save-board writes the board's editor-state file
    # beside it. It is hashed and reported, and never delivered.
    editor_state = PurePosixPath(request['board']).stem + '.kicad_prl'
    if editor_state in cand_files and editor_state not in ref_files:
        report_state = {editor_state: cand_files.pop(editor_state)}
    else:
        report_state = {}
    if set(cand_files) != set(ref_files):
        problems.append('candidate file set differs from the reference snapshot')
    for rel in sorted(set(cand_files) & set(ref_files)):
        if rel != request['board'] and cand_files[rel] != ref_files[rel]:
            problems.append(f'candidate changed a non-board project file: {rel}')
    prefix = request['project'] + '/'
    for rel, sha in ref_files.items():
        if inputs.get(prefix + rel) != sha:
            problems.append(f'reference snapshot file differs from the captured input: {rel}')
    for name in (request['board'], request['schematic']):
        if name not in ref_files:
            problems.append(f'reference snapshot lacks {name}')
    snapshot = load_json(run_dir / 'snapshot.json', problems, 'snapshot.json')
    if not isinstance(snapshot, dict) or snapshot.get('files') != ref_files:
        problems.append('snapshot.json files differ from the reference snapshot')
    if fingerprint(ref_files) != result['input'].get('fingerprint'):
        problems.append('input fingerprint differs from the reference snapshot')
    sha = cand_files.get(request['board'])
    validation = result['validation']
    if sha is None or not validated_candidate(validation, sha) or validation.get('restored_project_files') != []:
        problems.append('candidate board is not the board the router validated')
    problems += terminal_problems(result)
    custody = result.get('copper_ownership')
    if isinstance(custody, dict) and custody.get('status') == 'bound':
        record = run_dir / COPPER_OWNERSHIP_RECORD
        records = result['evidence'].get('records') if isinstance(result.get('evidence'), dict) else None
        if (record.is_symlink() or not record.is_file() or digest(record) != custody['sha256'] or
                not isinstance(records, dict) or records.get(COPPER_OWNERSHIP_RECORD) != custody['sha256']):
            problems.append('copper ownership record differs from its result pointer')
    return problems, sha, report_state, inventory


def retain(run_dir, dest):
    """Copy the attempt's diagnostics, regular files only, no design files.

    Design files are observed by digest and not copied (omitted), including the
    prepared backend input under work/input. There a design-file name that is not a
    regular file is observed by its path type alone, never followed: 'link' for a
    symbolic link (valid, dangling or looping alike), 'special' for a directory or
    special file, so an absent optional file (no entry) stays distinct from a linked
    or unresolvable one (agent-510 R4). Other links under work/input are ignored."""
    retained, omitted, links = {}, {}, []
    if run_dir is None:
        return retained, omitted, links
    for name in RETAINED_FILES:
        src = run_dir / name
        if src.is_symlink():
            links.append(name)
        elif src.is_file():
            (dest / name).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest / name)
            retained[name] = digest(dest / name)
    for top in RETAINED_DIRS:
        root = run_dir / top
        if root.is_symlink():
            links.append(top)
            continue
        if not root.is_dir():
            continue
        for rel, entry in tree(root).items():
            name = f'{top}/{rel}'
            if top == 'work' and PurePosixPath(rel).parts[0] == 'input':
                # The prepared backend input (after protection and preprocessing) is
                # observed, never copied: its regular design files by digest only, so
                # experiment_grade can bind the sidecar's effective input to it.
                if name.endswith(DESIGN_SUFFIXES):
                    omitted[name] = entry[1] if entry[0] == 'file' else PATH_TYPES.get(entry[0], 'special')
                continue
            if entry[0] in ('link', 'other'):
                links.append(name)
            elif entry[0] == 'file':
                if name.endswith(DESIGN_SUFFIXES):
                    omitted[name] = entry[1]
                    continue
                (dest / name).parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root / rel, dest / name)
                retained[name] = digest(dest / name)
    return dict(sorted(retained.items())), dict(sorted(omitted.items())), sorted(links)


def _executable(path):
    path = Path(path)
    try:
        real = path.resolve(strict=True)
    except OSError:
        return {'path': str(path), 'resolved': None, 'sha256': None}
    return {'path': str(path), 'resolved': str(real), 'sha256': digest(real) if real.is_file() else None}


def _identities(request, layout, generation=None):
    def identity_of(root):
        return tree_identity(root) if root.is_dir() and not root.is_symlink() else None

    checkout, state = Path(layout['checkout']), Path(layout['state'])
    identities = {'code': {top: identity_of(checkout / top) for top in ('bin', 'src')},
                  'state': {comp: identity_of(state / comp)
                            for comp in state_components(request['backend'], generation)}}
    manifest = state / 'manifest.json'
    identities['state_manifest'] = digest(manifest) if manifest.is_file() else None
    return identities


class _Cancelled(Exception):
    pass


def run_tool(argv, env, cwd, stdout, stderr, timeout, cancel):
    """Run the router in its own process group; kill the whole group on timeout or cancel."""
    record = {'argv': argv, 'cwd': str(cwd), 'env': env, 'timeout_s': timeout,
              'exit_code': None, 'signal': None, 'timed_out': False, 'cancelled': False}
    start = time.monotonic()
    with open(stdout, 'wb') as out, open(stderr, 'wb') as err:
        try:
            proc = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=out,
                                    stderr=err, start_new_session=True)
        except OSError as exc:
            record.update(error=f'{type(exc).__name__}: {exc}', duration_s=0.0)
            return record
        try:
            while proc.poll() is None:
                if cancel['requested']:
                    raise _Cancelled
                if time.monotonic() - start > timeout:
                    raise subprocess.TimeoutExpired(argv, timeout)
                time.sleep(0.05)
        except (subprocess.TimeoutExpired, _Cancelled) as exc:
            record['timed_out' if isinstance(exc, subprocess.TimeoutExpired) else 'cancelled'] = True
            _kill_group(proc.pid)
            proc.wait()
        _kill_group(proc.pid)  # descendants still in the group must not outlive the attempt
    code = proc.returncode
    if code < 0:
        record['signal'] = -code
    else:
        record['exit_code'] = code
    record['duration_s'] = round(time.monotonic() - start, 3)
    record['pid'] = proc.pid
    return record


def _kill_group(pgid):
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def write_report(path, report):
    report = dict(report)
    report.pop('identity', None)
    report['identity'] = canonical_identity(report)
    temp = path.with_name(path.name + '.tmp')
    temp.write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + '\n')
    temp.replace(path)
    return report


def constraint_binding_problems(candidate_root, request):
    """Refuse unmatched exact constraint assignments before invoking the router."""
    project = candidate_root / request['project'] / Path(request['board']).with_suffix('.kicad_pro')
    if not project.is_file():
        return []  # Native router policy handles projects without a settings file.
    try:
        value = json.loads(project.read_bytes())
        if (isinstance(value, dict) and isinstance(value.get('net_settings', {}), dict) and
                value.get('net_settings', {}).get('netclass_patterns', []) == []):
            return []
        if __package__:
            from .net_binding import require_project_nets
        else:
            # These immutable sibling files are included in the step's hashed mounts.
            sys.path.insert(0, str(Path(__file__).parent))
            from net_binding import require_project_nets
        board = candidate_root / request['project'] / request['board']
        require_project_nets(value, board.read_bytes())
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return [f'net constraint binding refused: {exc}']
    return []


def attempt(request, layout, cancel=None):
    """Run one attempt and write routing/adapter-report.json. Returns the report."""
    cancel = cancel if cancel is not None else {'requested': False}
    work = Path(layout['work'])
    routing = work / 'routing'
    if os.path.lexists(routing):
        raise RequestError('routing/ already exists; an attempt needs a fresh private OUT')
    for sub in ('home', 'tmp', 'attempt'):
        (routing / sub).mkdir(parents=True)
    report = {'schema': REPORT_SCHEMA, 'adapter': {'compatibility': f'kicad-route@{layout["pin"]}'},
              'request': request, 'reproducibility': REPRODUCIBILITY[request['backend']],
              'tool_version': dict(TOOL_VERSION), 'backend': dict(NO_BACKEND),
              'process': None, 'discovery': None, 'missing_artifacts': [], 'retained': {},
              'omitted_design_files': {}, 'candidate': None, 'candidate_editor_state': {},
              'candidate_files': {}, 'rejected_candidate_sha256': None, 'identity_after': None}
    candidate_root, checkout, state = Path(layout['candidate']), Path(layout['checkout']), Path(layout['state'])
    problems = []

    # Preconditions: nothing runs unless the input, code, state and mounts qualify.
    try:
        inputs_tree = tree(candidate_root)
    except (OSError, ValueError) as exc:
        inputs_tree, problems = {}, [f'candidate inputs unreadable: {exc}']
    inputs = files_of(inputs_tree)
    report['inputs'] = {'files': inputs, 'sha256': canonical_identity(inputs_tree), 'staged': None}
    if links_of(inputs_tree):
        problems.append('candidate inputs contain links or special files')
    project = request['project']
    for name in (request['board'], request['schematic']):
        if f'{project}/{name}' not in inputs:
            problems.append(f'candidate lacks {project}/{name}')
    problems += constraint_binding_problems(candidate_root, request)
    commit = None
    try:
        commit = checkout_commit(checkout)
    except OSError as exc:
        problems.append(f'cannot read the checkout HEAD: {exc}')
    report['tool'] = {'commit': commit, 'pin': layout['pin'],
                      'kicad_route': _executable(checkout / 'bin' / 'kicad-route'),
                      'python': _executable(PYTHON), 'kicad_cli': _executable('/usr/bin/kicad-cli'),
                      'kicad_share_templates': {t: _executable(f'/usr/share/kicad/template/{t}')['sha256']
                                                for t in ('sym-lib-table', 'fp-lib-table')}}
    if commit != layout['pin']:
        problems.append(f'checkout HEAD {commit} is not the pinned {layout["pin"]}')
    code_problems = verify_code(checkout, layout['code'])
    problems += code_problems
    if not code_problems:
        problems += verify_state(checkout, state, request['backend'], layout['code'], mounted_at=state)
    generation, selection = state_generation(state)
    problems += selection
    for rel in state_required(request['backend'], generation):
        if not os.path.lexists(state / rel):
            problems.append(f'missing installed backend dependency: KICAD_ROUTE_STATE/{rel}')
    targets = layout['probe'] if layout['probe'] is not None else (
        [state] + [state / c for c in state_components(request['backend'], generation) if (state / c).is_dir()] +
        ([checkout] if checkout != state else []))
    report['state_write_probe'] = write_probe(targets)
    if not all(p['refused'] for p in report['state_write_probe']):
        problems.append('the tool state or checkout is writable from the sandbox')
    report['identity_before'] = _identities(request, layout, generation)
    if not problems:
        # The router reads a staged copy of the verified input, never the mount itself.
        try:
            staged = stage_original(candidate_root, inputs_tree, staged_root(layout))
        except (OSError, ValueError) as exc:
            problems.append(f'cannot stage the original input: {exc}')
        else:
            report['inputs']['staged'] = staged_record(request, layout, staged)
            if staged != inputs_tree:
                problems.append('the staged original differs from the captured input')
    if problems:
        report['semantics'] = {'classification': 'not_run', 'reasons': problems, 'exit_code': None}
        experiment = experiment_grade(request, None, routing / 'attempt')
        if experiment is not None:
            report['semantics']['experiment'] = experiment
        return _finish(report, routing)

    argv, env = tool_argv(request, layout), tool_env(layout)
    if 'experiment_policy' in request:
        # The router reads its routability-policy/1 file here; verify re-checks the bytes.
        (routing / 'attempt' / EXPERIMENT_POLICY_FILE).write_text(canonical_json(request['experiment_policy']))
    report['process'] = run_tool(argv, env, routing, routing / 'kicad-route.stdout', routing / 'kicad-route.stderr',
                                 request['timeout_s'], cancel)
    report['identity_after'] = _identities(request, layout, generation)
    after_inputs = tree(candidate_root)
    run_dir, found = discover(routing / 'out', request)
    report['discovery'] = {'attempt_dir': run_dir.relative_to(routing).as_posix() if run_dir else None,
                           'problems': found}
    retained, omitted, links = retain(run_dir, routing / 'attempt')
    report['retained'] = {f'attempt/{k}': v for k, v in retained.items()}
    policy_file = routing / 'attempt' / EXPERIMENT_POLICY_FILE
    policy_kept = 'experiment_policy' in request and policy_file.is_file() and not policy_file.is_symlink()
    if policy_kept:
        report['retained'][f'attempt/{EXPERIMENT_POLICY_FILE}'] = digest(policy_file)
    report['omitted_design_files'] = omitted
    report['missing_artifacts'] = missing_reports(retained)
    load = []
    result = load_json(routing / 'attempt/result.json', load, 'result.json') if 'result.json' in retained else None
    attempt_record = load_json(routing / 'attempt/attempt.json', load, 'attempt.json') if 'attempt.json' in retained else None
    report['backend'] = backend_of(result)
    sidecars, sidecar_links = retain_experiment(run_dir, routing)
    for sidecar in sidecars:
        report['retained'][f'attempt/{sidecar}'] = digest(routing / 'attempt' / sidecar)
    links += sidecar_links
    experiment = experiment_grade(request, result, routing / 'attempt',
                                  experiment_run(request, layout, report['discovery']['attempt_dir'],
                                                 report['omitted_design_files']))
    stdout = (routing / 'kicad-route.stdout').read_bytes().decode('utf-8', 'replace')
    refused_line = refused_output(stdout)
    process = report['process']
    if process['cancelled']:
        classification, reasons = 'cancelled', ['attempt cancelled; the router process group was killed']
    elif process['timed_out']:
        classification, reasons = 'timeout', [f'router exceeded the {request["timeout_s"]} s adapter timeout']
    else:
        classification, reasons = semantics(process['exit_code'], result, attempt_record, refused_line,
                                            run_dir is not None, request)
    violations = found + load + [f'link or special file in attempt diagnostics: {n}' for n in links]
    violations += runtime_problems(request, layout, generation, result)
    if report['identity_after'] != report['identity_before']:
        violations.append('tool code or state changed during the attempt')
    if after_inputs != inputs_tree:
        violations.append('candidate inputs changed during the attempt')
    try:
        after_staged = tree(staged_root(layout))
    except (OSError, ValueError):
        after_staged = None
    if after_staged != inputs_tree:
        violations.append('the staged original changed during the attempt')
    if 'experiment_policy' in request and (
            not policy_kept or policy_file.read_bytes() != canonical_json(request['experiment_policy']).encode()):
        violations.append('experiment policy file changed during the attempt')
    if experiment is not None and experiment['status'] == 'refused':
        violations.append(f'routability experiment evidence refused ({experiment["reason"]}): '
                          f'{experiment["detail"]}')
    sha = None
    if classification in ('complete', 'incomplete'):
        bind, sha, report['candidate_editor_state'], report['candidate_files'] = bind_candidate(
            request, layout, run_dir, result, attempt_record, inputs)
        violations += bind + [f'eligible attempt lacks {n}' for n in report['missing_artifacts']]
    elif classification == 'validation_failed' and run_dir is not None:
        board = run_dir / attempt_record['candidate_dir'] / request['board']
        if board.is_file() and not board.is_symlink():
            report['rejected_candidate_sha256'] = digest(board)  # diagnostics only; never copied
    if violations and classification not in ('timeout', 'cancelled'):
        classification, reasons = 'contradiction', reasons + violations
        # A contradiction rejects no candidate: the validation_failed diagnostic hash goes.
        report['rejected_candidate_sha256'] = None
    elif violations:
        reasons = reasons + violations
    policy, grade = request.get('layer_policy'), None
    if policy is not None:
        if classification in ('complete', 'incomplete') and 'reference' in policy:
            board = run_dir / attempt_record['candidate_dir'] / request['board']
            # retain() copies only the router's named reports, so these names are agent-ee's.
            if digest(board) == sha:
                measure_reference(policy, board, routing, tool_env(layout))
                for name in (POLICY_FILE, Path(POLICY_FILE).with_suffix('.log').name):
                    path = routing / 'attempt' / name
                    if path.is_file() and not path.is_symlink():
                        report['retained'][f'attempt/{name}'] = digest(path)
        grade = layer_policy_grade(policy, routing / 'attempt', result, classification, request['board'])
        if classification in ('complete', 'incomplete') and grade['status'] != 'pass':
            failing = [f'{k} {v["status"]}' for k, v in grade['items'].items() if v['status'] != 'pass']
            classification, reasons = 'policy_failed', reasons + [
                f'declared layer policy {grade["status"]}: ' + ', '.join(failing)]
            report['rejected_candidate_sha256'] = sha  # diagnostics only; never copied
            report['candidate_files'], report['candidate_editor_state'] = {}, {}
    if classification in ('complete', 'incomplete'):
        dest = routing / 'candidate' / project / request['board']
        dest.parent.mkdir(parents=True)
        shutil.copyfile(run_dir / attempt_record['candidate_dir'] / request['board'], dest)
        if digest(dest) != sha:
            classification, reasons = 'contradiction', reasons + ['candidate changed while being copied']
            shutil.rmtree(routing / 'candidate')
        else:
            report['candidate'] = {'path': f'candidate/{project}/{request["board"]}', 'sha256': sha}
    report['semantics'] = {'classification': classification, 'reasons': reasons,
                           'exit_code': process['exit_code']}
    if grade is not None:
        report['semantics']['layer_policy'] = grade
    if experiment is not None:
        report['semantics']['experiment'] = experiment
    return _finish(report, routing)


def _finish(report, routing):
    for name in ('kicad-route.stdout', 'kicad-route.stderr'):
        if (routing / name).is_file():
            report['retained'][name] = digest(routing / name)
    report['retained'] = dict(sorted(report['retained'].items()))
    proposal, coverage, code = CLASSES[report['semantics']['classification']]
    report['semantics'].update(proposal=proposal, coverage=coverage, adapter_exit=code)
    result = load_json(routing / 'attempt/result.json', [], 'result')
    record = load_json(routing / 'attempt/attempt.json', [], 'attempt')
    report['semantics']['pair_coverage'] = pair_coverage(
        report['request'], result, record, report['semantics']['classification'])
    policy = report['request'].get('layer_policy')
    if policy is not None and 'layer_policy' not in report['semantics']:
        report['semantics']['layer_policy'] = layer_policy_grade(
            policy, routing / 'attempt', result, report['semantics']['classification'], report['request']['board'])
    return write_report(routing / 'adapter-report.json', report)


def main(argv=None, layout=None):
    argv = sys.argv[1:] if argv is None else argv
    try:
        request = parse_request(argv)
    except RequestError as exc:
        print(f'kicad-route adapter: {exc}', file=sys.stderr)
        return EXIT_REQUEST
    layout = layout or default_layout(request)
    cancel = {'requested': False}
    previous = signal.signal(signal.SIGTERM, lambda *_: cancel.__setitem__('requested', True))
    try:
        report = attempt(request, layout, cancel)
    except RequestError as exc:
        print(f'kicad-route adapter: {exc}', file=sys.stderr)
        return EXIT_REQUEST
    except Exception:
        # An adapter fault must not read as the router's own exit 1.
        import traceback
        traceback.print_exc()
        return EXIT_ADAPTER_ERROR
    finally:
        signal.signal(signal.SIGTERM, previous)
    sem = report['semantics']
    print(json.dumps({'classification': sem['classification'], 'proposal': sem['proposal'],
                      'router_exit': sem['exit_code'], 'adapter_exit': sem['adapter_exit']}))
    return sem['adapter_exit']


if __name__ == '__main__':
    sys.exit(main())
