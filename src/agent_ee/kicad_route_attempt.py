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
``--experiment-policy`` names only the adapter-written routability-policy/1 file
(AGENTEE-442).

The report is provenance. It never makes a check pass: acceptance comes only
from agent-ee's own fresh checks on the captured proposal.
"""

import collections
import errno
import hashlib
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath

PIN = '5f1c072b86e0caee82c7656226c4d0e3ec580a58'
# SHA-256 of every tracked bin/ and src/ blob at PIN, including the owned engine.
PINNED_CODE = {'bin/kicad-route': '4d01520182f391ff1f92be7eb1e2415d3675a309a38ef449012d49e2d03e42e9',
 'src/kicad_route/__init__.py': '23611cbf0bfd79c20f1e18fa402bbe8c1bcd63b412fbf84e7eb3b5f3c0062af4',
 'src/kicad_route/barrier.py': 'de34c57c391248c4fdb02fdd9f71d6736edbc15621350be1ff188bc8b217df6c',
 'src/kicad_route/blocked.py': 'b2dc298ca38dd994479afdb6ced91d8196b0fd1626f2828c0996135ecb57c7f5',
 'src/kicad_route/campaign.py': '5cfad4d863c03578aa02fb2221598e8dcab73b2ac673124f8ff9d440ab4d2aa6',
 'src/kicad_route/canary.py': '38e56e7bd96f01025f9ca8a3c57d4f902f939723f55f50f214395561c3cbbf1c',
 'src/kicad_route/cli.py': '5cff5eb1b1e78b1c7ef6917f827cf598542886bf83a24f964dbb67c4929358ab',
 'src/kicad_route/common.py': 'a42c0258052acc83004e921c139881bd0aa0ac4daa26f268348b04ee8dd2f923',
 'src/kicad_route/contract.py': '306516b16cc6a55ce62d1915f354c298297bc944f4ae5eccc24a0d49bb83b2b6',
 'src/kicad_route/corpus.py': 'f63cedc1ec87b37b06c1a717f2eb02ec57af34a659b8f7e9e330eff5736bda14',
 'src/kicad_route/decoupling.py': '29724e237cc94c0af783619991dc5659b4b582eb6cdb33fd0d59542a108232f6',
 'src/kicad_route/determinism.py': '653d6c37b4e69d50cd701223e23c6feb2e8afd5b240b5bdd5a5f4c1146af5778',
 'src/kicad_route/diff_pairs.py': 'f1d1070ed0acc459d28cbe7397ae7c22b196f5dbb869e751af24e93a3c1b1092',
 'src/kicad_route/engine.py': '5e2ecaae2ad0b2b4119a2787283e4c319581d154b59d5617b74f205db9914bdb',
 'src/kicad_route/evidence.py': 'd2f1acb31df60b1874b565c341378b25962570b6c12689d6b029f7e348b96f8b',
 'src/kicad_route/floor.py': '2f954a2d1142c4f68d7e2904781f4470c0976af925a0a91c31c8de26d4f31ae6',
 'src/kicad_route/kicad/barrier.py': 'ffeab974f15db240d55c1d3e8a19450d885be7ab33b7f595fdf79c5f6e2409e4',
 'src/kicad_route/kicad/decoupling.py': '498624e3f38485519865bb871a01e3f62fc3a41c9bde80d2966ae3c05f7e88b5',
 'src/kicad_route/kicad/facts.py': 'cbc90a12bfbbfcb5f9ff4637396992faff128a35a9d13ff494184a5d6e57b316',
 'src/kicad_route/kicad/locks.py': '5b15d4ddfbec9b7e32a841a16f9c1dd383b043c240a293251737e9cba218800a',
 'src/kicad_route/kicad/pairs.py': '44763bfd3b25871cf9c5b5636d97f4534157920ac83ca755c3d57628638552f9',
 'src/kicad_route/kicad/planes.py': '8498b1566edda7d29631210435669737a22d990df7fbd3c487c92a6827f5f07d',
 'src/kicad_route/kicad/pour.py': 'a49f64c51854720e8ea680d81b94969dc8242da0c3ee93b72c8948a57b908c14',
 'src/kicad_route/kicad/rename_nets.py': 'a51c1767c0ce1d0edf180700a026d695678228d56ece4bab688d9bfd3d23d2a8',
 'src/kicad_route/kicad/specctra.py': '0cd5031726ab48c7b3c2bf1dd3978837574d7452d7a79dda154123acb1aeb9d1',
 'src/kicad_route/kicad/spokes.py': 'c419ea1123a6de2588358aa58d0dfaabd4d5f55d50d559023ce48d2271f12bd9',
 'src/kicad_route/ledger.py': '37f140a1b6ccbf6c6ffa6724ea8acdf97e404eabd5ddb8c8c264a19cc1e6f752',
 'src/kicad_route/netnames.py': '731bcf61a909ef2720fd90b074f6cfb991a1c6ea6bea7fecf5ba3cb7fc47304f',
 'src/kicad_route/plane_plan.py': 'fab213cc155347af3c36bda5694b7aa871058c6f8b77eaf52ebd2b209bfb2d80',
 'src/kicad_route/planes.py': '2adaab4efe91f380723efdf3af913e90dba90453b6db5792f5506b93956d3563',
 'src/kicad_route/pour.py': '630b957fddeab33d108d634fb0f229f3c1471f80953e971effad27ca33beb2e9',
 'src/kicad_route/procgroup.py': 'd843c9b334874d0334c5ea0e9a58d83d22581648e915d40f13a6d8c8b595a3d5',
 'src/kicad_route/provenance.py': 'cf82fd9940086584a8056787ae0ca4fd58ced78ed5737f85b4e5713501add49f',
 'src/kicad_route/router/.claude/skills/diagnose-routing-failures/SKILL.md': '0abb55757415b58f1c45aae2b0c9c09368541cb5611c2621e362ef15f5349b14',
 'src/kicad_route/router/.claude/skills/review-routed-board/SKILL.md': '29e7c0e3a11ead24f35e17abf1a1acbca30f8d33d00ba06aca41d4a92c4c0f20',
 'src/kicad_route/router/LICENSE': '6df0be9100e852f1749d2e88df423b0f71cab5bff7af35f749e10cf644911390',
 'src/kicad_route/router/UPSTREAM.json': 'ce4c0c19523c84c080bb7c6219ec5616910fad95888cc70d5ac10cb5fce36f08',
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
 'src/kicad_route/router/py_router/blocking_analysis.py': '83daa554a378cc337cd92cba8569659f1f00ff9ead65ee61ebac444c3aee3db5',
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
 'src/kicad_route/router/py_router/diff_pair_multipoint.py': '5468ddcedf1fdc3355d7451adaba08f4e982ddde074facef0c54aba54ad7871d',
 'src/kicad_route/router/py_router/diff_pair_routing.py': 'aa355c8a1a10fbb672000a78dfa7974bb7ab95fc9c079eca716639586d6726a0',
 'src/kicad_route/router/py_router/diff_xnet.py': '204665ebc1494103a7e6960aa43bfd0eada9d17c70d31dc6cda8be63e5909f24',
 'src/kicad_route/router/py_router/dup_trap.py': 'f3a3cc3b91d7318b46a091d14586ce31469c3101ddcd61b325bbda90e63fa1ee',
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
 'src/kicad_route/router/py_router/kicad_parser.py': 'f85b1b8f2bc85f418dea0ba1aac6c35a7c89863026c3e91953214f7d15b83ade',
 'src/kicad_route/router/py_router/kicad_writer.py': 'ffce00f68e84b4a37f393a21c995036f9301f43778e0b78728106bb7644425c5',
 'src/kicad_route/router/py_router/layer_swap_fallback.py': '4fa52734ddd7879ff71a5b0f43819750e4b4fb56f2295b72bd40fee477715b5f',
 'src/kicad_route/router/py_router/layer_swap_optimization.py': 'fa1f6f93960a4d87f17d7acb348d55183bb60688ab6ba0700bb0f3c32df460bc',
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
 'src/kicad_route/router/py_router/net_ordering.py': 'bec77b33cf11177d8fc276ad0d47a531110b9854004e764d1dfa331464f40aa0',
 'src/kicad_route/router/py_router/net_queries.py': '6b47c762cfe67ca0b0f432a4b47f149d52f82a10c2f5d8a1781d26b652275cd9',
 'src/kicad_route/router/py_router/net_rescue.py': '38fca12fce416c441c80823809390be9a3a10bf591aae25525ef34544e9514be',
 'src/kicad_route/router/py_router/net_story.py': 'a54ca5aff45e80d8a27b8b15fa4f4576a2bda803a8ce2ab4b7613e94101674ac',
 'src/kicad_route/router/py_router/obstacle_cache.py': 'dea6d6315a9fdd69413fbaefabafb73b79979e5bac50826c44e80830f3b6c6d3',
 'src/kicad_route/router/py_router/obstacle_costs.py': '66afaf065eeafe21af70b361cc2f0cf64c9a6efb7da1b418b0379167093358ec',
 'src/kicad_route/router/py_router/obstacle_map.py': '4fa8ab6598268267cc286047f05be90bc2b355cd33d87a083b6db8f1e0ff4757',
 'src/kicad_route/router/py_router/output_writer.py': '617d58a2125558419a380557c8628ad8fba3cf8d0f31efe0872ed4f2fa4bdaaf',
 'src/kicad_route/router/py_router/pack_river.py': '34d54fb0f9ae956ab0507fa818cf604fdc8816a0b54508f23db9c742e7c31dc0',
 'src/kicad_route/router/py_router/paste_apertures.py': '0c7a864b934ae7ec812f0fb091d16385f442b8472fa34885b85f20219e7e6031',
 'src/kicad_route/router/py_router/pcb_io_utils.py': 'a128f2dc42e869dc46ac240d739e6523eb31a75ffec85798c396784ab7606892',
 'src/kicad_route/router/py_router/pcb_modification.py': '3c8da17cd58610d263a77a8703ff09df27974ff86d19e9162876089c00212a20',
 'src/kicad_route/router/py_router/phase3_routing.py': 'fa208fc1ffe6d446e8a5eccd54c56c1836ddf3345564093a253f749577e134e4',
 'src/kicad_route/router/py_router/place_motion.py': '9fa0574956f290e947a1c842f0d6ffe38a7e8ff42b881ecf7b94780e78d00f09',
 'src/kicad_route/router/py_router/plane_blocker_detection.py': 'c439b516ff466034902153e29a52969d16002891506ada7d6b61d5f7ca1931d1',
 'src/kicad_route/router/py_router/plane_component_oracle.py': '845aee20a9d3080b1047f48023bb93fb9cebfb072962bca64e6a0a2f208ebf96',
 'src/kicad_route/router/py_router/plane_corridor_ghosts.py': 'dd2e5cc64a7946af47cdf98b6da9a5ac530eff2a64619c4774c2c81ce208a2e1',
 'src/kicad_route/router/py_router/plane_fill_model.py': '001778d66ca3b4b01309c3bb979fcba6a989d07495dcad1c7662b74fee00777a',
 'src/kicad_route/router/py_router/plane_fragility.py': 'c5d4fe82872c8767abb07c2a439dbf76701b0b426402469e2b7e01d4b081cd60',
 'src/kicad_route/router/py_router/plane_io.py': '0155a404aaf856d9256282d1115a257b923eeff7ad87620b19efe95d942b9169',
 'src/kicad_route/router/py_router/plane_lattice.py': 'c96af256f7f1827cf7b5d2a1ec472eda1a841f61bfa817307cb762765015c353',
 'src/kicad_route/router/py_router/plane_obstacle_builder.py': 'cbc959ff99e9c666913037e7c40c456f166feba549220e166b9f0a64b67dbdd7',
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
 'src/kicad_route/router/py_router/repair_planes.py': '5a5fdd7906ef5ad3acbbf6a0b72a08dd64fe3c4a4fee107e20a117d9432d0fbc',
 'src/kicad_route/router/py_router/reroute_loop.py': '7b5002ba9c5b412784372f3cd8c789bdec370180ba17c370506def89581bf2cd',
 'src/kicad_route/router/py_router/rip_defer.py': 'c965f8b2cdf65d10254735049b5eebafe72c4229d9d9efbedd10bd0dd06dfdb6',
 'src/kicad_route/router/py_router/rip_restore.py': '9a5a68cf13050035de319d8158807cea3104b79241ef15523a729edf42ae544b',
 'src/kicad_route/router/py_router/rip_up_reroute.py': 'e438891f67059b0376eae91c4bff82bb26f2dec9e702aee454927abc29cc5149',
 'src/kicad_route/router/py_router/route.py': 'a5b387a0cc562fbe2899efd25bac563a0abf1df02896d5b2bbe42044930cff1e',
 'src/kicad_route/router/py_router/route_diff.py': '3da7ecc9a60de4a7521d7dd5f598b80a5e768e669f80c46e9c582af9c8826ff8',
 'src/kicad_route/router/py_router/route_planes.py': '468d17b55a84161bc70ad8594dfc5142e2416addda501f8b8cd6aa6b25940cb3',
 'src/kicad_route/router/py_router/route_render.py': '5fca0a39fbca4d58e0a77f503867e0fb48bed766ede31b9664a5a0b6c9f7131f',
 'src/kicad_route/router/py_router/route_summary.py': '3c8575595d90ca286dc06e36df2905aea583a10e986d13633fd36b5e8d026410',
 'src/kicad_route/router/py_router/route_trace.py': '4511cbacf524a9b72739ed06e4d1b1ff28cf035fef5aa634939cfba3f0c73e3b',
 'src/kicad_route/router/py_router/routing_common.py': 'a698bbb0c09d6be2bb9e87e741e4cb23bb563e0fe9a7ebe6ed29356a00166311',
 'src/kicad_route/router/py_router/routing_config.py': '3165f32065b639825515b00a7df6716228faef7c72c94f6c14cd88bf3b322719',
 'src/kicad_route/router/py_router/routing_constants.py': '5bd4f2aa4d42b5b8f581111bd2ef534a5a1aa53af81b7757fbecfff529c2f718',
 'src/kicad_route/router/py_router/routing_context.py': '2285fa2acee5d0cd1893dd0d95f100b54abd35a7fc625849ab69b4d050911f0e',
 'src/kicad_route/router/py_router/routing_defaults.py': '814cb2693c8029539b9a37726fa84c8b93ed76edbd32da31ac6722a80e75ddf9',
 'src/kicad_route/router/py_router/routing_diagnostics.py': '82aef147c608c83d897b8713073893b722bb71d0880f8575ac1c3d94afc46b15',
 'src/kicad_route/router/py_router/routing_exceptions.py': '2f881ffd0ea1eb1bb19d49c300d1ddac5a2da87a20403f62cc6026c9a5f4c03a',
 'src/kicad_route/router/py_router/routing_state.py': '38b8ce2090aa5029bf63ef657b3ef62ce66f03a553f193f570b442dd60f44c01',
 'src/kicad_route/router/py_router/routing_utils.py': 'a7d25f7423f4fcddc5be3ebbfedc31f3c70ae895963c23740679489ebd75e536',
 'src/kicad_route/router/py_router/rust_alloc.py': '838d142cd07a85f8f78b49b8a237f783525c1a548f545195aaa3008331a7655e',
 'src/kicad_route/router/py_router/schematic_updater.py': '3dc6cccbadd5e61ee99b08c11557566cb030d6cadf9916352357df6bf52582d8',
 'src/kicad_route/router/py_router/single_ended_loop.py': '7e8dc8d72175fb067781434e6c2c673fcda6482b7a0465f1ed3fc7fedb7bc4c5',
 'src/kicad_route/router/py_router/single_ended_routing.py': 'a0f5cf37746f694a693e5fba39530eacd09a257385953443a195a570d889a9a5',
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
 'src/kicad_route/router/py_router/stub_layer_switching.py': '42b46236da915cbe1f3c3468c1fba09134cc02b87ee02af48f38c87579e7df5d',
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
 'src/kicad_route/router/rust_router/src/obstacle_map.rs': 'f5abe681accc32a7870101c6b776058ece72f5b90deaca5180dba688db7ce471',
 'src/kicad_route/router/rust_router/src/pair_search.rs': 'f384c7331e677f6821176da6d36d3e51a00fd4bb9f06151e7520963b463c5204',
 'src/kicad_route/router/rust_router/src/pose_router.rs': 'a4df246ef8a886d1cd3df989734bfd4a15ddc72e551905b046fbbe056c240a4c',
 'src/kicad_route/router/rust_router/src/router.rs': 'd3cb9ac9cfa5d25180f29d92f4c55a9082393ac67c9c81d9968ab26177d0681d',
 'src/kicad_route/router/rust_router/src/types.rs': '70058395d6c08ccefbe7b26596025adb60724767565182d8eca2db42ebccbfe4',
 'src/kicad_route/router/tests/test_krt64_tap_grid.py': '65e10213bd0429937972ed976d67667ab3b63b79714fa0ee170c547adeaf0c1c',
 'src/kicad_route/router/tests/test_pour_launch_forbidden_layer_welds.py': 'be2c95e968234c53a85c07c1fea626a868dbb28211cf4b88d91335c00e728a2b',
 'src/kicad_route/sandbox.py': '5668f18e1b25d81d96f1568ee2e9fe8508a0abbaa63c28b8573dc3ebfa27edc6',
 'src/kicad_route/skills.py': 'b261fcb7596db3921c089af549a77d5803ba6c0e7590132026e9fdb85106cee8',
 'src/kicad_route/snapshot.py': '7c51203f4a439cc99a575e6bc5433f3ab78dda7735dcd03c940f6169fe7fb9a4',
 'src/kicad_route/spokes.py': 'fc5b3f90c31cbcc9a9944628bfe6b9bc93dc31de21c893f8e476160f21033506',
 'src/kicad_route/stall.py': '095445837a1621ca8eaa62fee2f87ec772d13fcb9111e75528743f24f095d188',
 'src/kicad_route/statemanifest.py': '1a9903902e7f0fb1771d2730f986076c692db9579bfcbc42a9c0b576e5777ae2'}
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
PYTHON = '/usr/bin/python3'
# KICADROUTE-82: 'builtin' is the canonical name of the owned backend and 'krt' its deprecated
# alias. A request or retained record keeps the name it was written with (receipts that say
# 'krt' keep their bytes); names are normalized only to compare or look up.
BACKEND_ALIASES = {'krt': 'builtin'}
BACKENDS = ('builtin', 'krt', 'freerouting')
# What the pinned cli.py accepts for --backend. 5f1c072 predates the rename, so both names of
# the owned backend are emitted as 'krt' until the pin moves; then this maps builtin to itself.
PIN_BACKEND_NAMES = {'builtin': 'krt', 'krt': 'krt', 'freerouting': 'freerouting'}


def canonical_backend(name):
    """The canonical spelling of a backend name; an unknown or non-string name is returned unchanged."""
    return BACKEND_ALIASES.get(name, name) if isinstance(name, str) else name


# Installed backend inputs the pinned cli.py reads under KICAD_ROUTE_STATE.
STATE_COMPONENTS = {'krt': ('.venv-krt',), 'builtin': ('.venv-krt',), 'freerouting': ('vendor/freerouting',)}
STATE_REQUIRED = {'krt': ('.venv-krt/bin/python',), 'builtin': ('.venv-krt/bin/python',),
                  'freerouting': ('vendor/freerouting',)}
SAFE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z')
STAMP = re.compile(r'\d{8}T\d{6}\.\d{6}Z\Z')
# Every top-level result.json/attempt.json key cli.run_attempt writes at PIN
# (protect=existing). Any other shape is an incompatible tool.
RESULT_KEYS = {'schema', 'measurement', 'evidence', 'tool_identity', 'sandbox', 'kind', 'backend',
               'input', 'settings', 'scope', 'unsupported', 'floor', 'process',
               'routing', 'validation', 'candidate', 'run_dir', 'runtime_s', 'tool_versions',
               'finished_utc', 'exit_code', 'ledger'}
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
# experiment = {path, sha256, schema}, naming a routability-experiment/1 sidecar
# in the attempt directory; --experiment-policy PATH gives the router a
# routability-policy/1 file. The adapter retains the sidecar with its recomputed
# identity or refuses it with a typed reason; it is provenance, never qualified.
# EXPERIMENT_CONTRACT is the agreed reference shape. Only the sidecar fields named
# here are read (schema, policy). They are provisional until agent-507 sends the
# final reviewed schema and producer SHA; tests/test_routing_experiment_boundary.py
# marks the hook to rerun against it.
EXPERIMENT_SCHEMA = 'routability-experiment/1'
EXPERIMENT_POLICY_SCHEMA = 'routability-policy/1'
EXPERIMENT_POLICIES = ('historical-all-layer', 'current-policy')
EXPERIMENT_REFERENCE_KEYS = {'path', 'sha256', 'schema'}
EXPERIMENT_POLICY_KEYS = {'schema', 'policy'}
EXPERIMENT_POLICY_FILE = 'agent-ee-experiment-policy.json'
EXPERIMENT_CONTRACT = 'agent-ee/AGENTEE-442 provisional: K91 M1b reference shape; sidecar body unreviewed'
EXPERIMENT_REFUSALS = ('missing', 'malformed', 'unknown_schema', 'tampered', 'incompatible_schema',
                       'incompatible_policy')
SHA256 = re.compile(r'[0-9a-f]{64}\Z')


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
    except (TypeError, json.JSONDecodeError):
        raise RequestError('layer policy must be JSON') from None
    validate_layer_policy(policy)
    if canonical_json(policy) != text:
        raise RequestError('layer policy must be canonical JSON (sorted keys, no spaces)')
    return policy


def parse_experiment_policy(text):
    """A declared routability-policy/1 document, unchanged, or RequestError.

    Any other schema version or policy name is incompatible and refused before
    the router runs."""
    try:
        policy = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        raise RequestError('experiment policy must be JSON') from None
    if not isinstance(policy, dict) or set(policy) != EXPERIMENT_POLICY_KEYS:
        raise RequestError('experiment policy must have exactly schema and policy')
    if policy['schema'] != EXPERIMENT_POLICY_SCHEMA:
        raise RequestError(f'incompatible experiment policy schema: expected {EXPERIMENT_POLICY_SCHEMA}')
    if policy['policy'] not in EXPERIMENT_POLICIES:
        raise RequestError(f'experiment policy must be one of {EXPERIMENT_POLICIES}')
    if canonical_json(policy) != text:
        raise RequestError('experiment policy must be canonical JSON (sorted keys, no spaces)')
    return policy


def experiment_policy_identity(policy):
    """The identity of the policy file the adapter writes for the router, or None."""
    if policy is None:
        return None
    return {'schema': policy['schema'], 'policy': policy['policy'],
            'sha256': hashlib.sha256(canonical_json(policy).encode()).hexdigest()}


def experiment_sidecar_name(result, run_dir, origin):
    """The sidecar file name a result.json experiment reference names, or None.

    The reported path is an untrusted string resolved lexically against origin;
    only a .json file directly inside the attempt directory, outside the names
    the adapter already retains or owns, can be a sidecar."""
    ref = result.get('experiment') if isinstance(result, dict) else None
    if run_dir is None or not isinstance(ref, dict):
        return None
    path = resolve_reported(ref.get('path'), origin, origin)
    if path is None or path.parent != Path(run_dir):
        return None
    name = path.name
    if (not SAFE.match(name) or not name.endswith('.json') or name in RETAINED_FILES or
            name.startswith('agent-ee-')):
        return None
    return name


def retain_experiment(result, run_dir, routing):
    """Copy the referenced sidecar into routing/attempt; its retained name, or None."""
    name = experiment_sidecar_name(result, run_dir, routing)
    if name is None:
        return None
    src = Path(run_dir) / name
    if src.is_symlink() or not src.is_file():
        return None
    shutil.copyfile(src, routing / 'attempt' / name)
    return name


def experiment_grade(request, result, attempt_dir, run_dir, origin):
    """The experiment-evidence record, re-derived from the retained bytes alone.

    None when no policy was declared and result.json references no sidecar.
    Otherwise 'retained' with the sidecar's recomputed identity, 'refused' with
    one of EXPERIMENT_REFUSALS, or 'absent' when the router wrote no result.json.
    Every record is qualified: false."""
    expected = experiment_policy_identity(request.get('experiment_policy'))
    has_ref = isinstance(result, dict) and 'experiment' in result
    if expected is None and not has_ref:
        return None
    base = {'qualified': False, 'contract': EXPERIMENT_CONTRACT, 'policy': expected,
            'policy_file': f'attempt/{EXPERIMENT_POLICY_FILE}' if expected is not None else None}
    if not isinstance(result, dict):
        return {'status': 'absent', **base, 'reason': 'no result.json: the router reported no experiment'}
    ref = result.get('experiment')

    def refused(reason, detail, **known):
        return {'status': 'refused', **base, 'reason': reason, 'detail': detail,
                'reference': ref, 'path': None, 'sha256': None, **known}

    if not has_ref:
        return refused('missing', 'a policy was declared but result.json has no experiment reference')
    if (not isinstance(ref, dict) or set(ref) != EXPERIMENT_REFERENCE_KEYS or
            not all(isinstance(ref[k], str) for k in EXPERIMENT_REFERENCE_KEYS)):
        return refused('malformed', 'experiment reference must have exactly string path, sha256 and schema')
    if ref['schema'] != EXPERIMENT_SCHEMA:
        return refused('unknown_schema', f'experiment reference schema is not {EXPERIMENT_SCHEMA}')
    if not SHA256.match(ref['sha256']):
        return refused('malformed', 'experiment reference sha256 is not 64 lowercase hex digits')
    name = experiment_sidecar_name(result, run_dir, origin)
    if name is None:
        return refused('malformed', 'experiment path is not a .json sidecar directly inside the attempt directory')
    kept = Path(attempt_dir) / name
    if kept.is_symlink() or not kept.is_file():
        return refused('missing', 'no regular sidecar file at the referenced path was retained')
    known = {'path': f'attempt/{name}', 'sha256': digest(kept)}
    if known['sha256'] != ref['sha256']:
        return refused('tampered', 'sidecar sha256 differs from the result.json reference', **known)
    problems = []
    body = load_json(kept, problems, 'experiment sidecar')
    if not isinstance(body, dict):
        return refused('malformed', '; '.join(problems) or 'experiment sidecar is not a JSON object', **known)
    if body.get('schema') != EXPERIMENT_SCHEMA:
        return refused('incompatible_schema', f'sidecar schema is not {EXPERIMENT_SCHEMA}', **known)
    if 'policy' not in body or body['policy'] != expected:
        return refused('incompatible_policy', 'sidecar policy identity differs from the declared policy', **known)
    return {'status': 'retained', **base, 'schema': EXPERIMENT_SCHEMA, 'reference': ref, **known}


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


def verify_state(checkout, state, backend='krt', pinned=None):
    """Verify the owned engine using the qualified tool's state-manifest reader.

    Called only after verify_code succeeds. Synthetic adapter-policy fixtures
    supply their own code map without an engine; production PINNED_CODE always
    includes it. No routing, installation or network is performed here.
    """
    pinned = PINNED_CODE if pinned is None else pinned
    if canonical_backend(backend) != 'builtin' or 'src/kicad_route/engine.py' not in pinned:
        return []
    script = r'''
import json, sys
from pathlib import Path
checkout, state = map(Path, sys.argv[1:])
sys.path.insert(0, str(checkout / 'src'))
from kicad_route import engine, statemanifest
m = statemanifest.read(state)
if m is None:
    raise ValueError('missing or unreadable state manifest')
problems = statemanifest.schema_problems(m)
k = m.get('krt') or {}
if k.get('source_sha256') != engine.source_sha256():
    problems.append('owned engine source digest differs')
module = (k.get('extension_module') or {}).get('path')
venv = state / '.venv-krt'
installed = statemanifest.extension_module(venv)
if (not module or installed is None or
        not (state / module).resolve().is_relative_to(venv.resolve()) or
        (state / module).resolve() != installed.resolve()):
    problems.append('manifest must identify the installed .venv-krt grid_router extension')
if not (k.get('extension_module') or {}).get('sha256'):
    problems.append('missing installed engine digest')
if (k.get('venv') or {}).get('path') != '.venv-krt':
    problems.append('unexpected routing environment path')
# The manifest was installed on the host. The immutable source is mounted at a
# different absolute path inside the sandbox; verify its recorded hash there.
if k:
    k['requirements']['path'] = str(engine.ROOT / 'requirements.txt')
problems += [f"state manifest drift: {d['item']}" for d in statemanifest.verify(m, state)]
print(json.dumps(problems))
'''
    try:
        # -B still reads caches. /dev/null cannot hold ignored bytecode.
        result = subprocess.run([PYTHON, '-I', '-B', '-X', 'pycache_prefix=/dev/null',
                                 '-c', script, str(checkout), str(state)],
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
        'backend': raw.get('--backend', 'krt'),
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
            '--dir', str(Path(layout['candidate']) / request['project']),
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
            'GIT_CEILING_DIRECTORIES': f'{layout["candidate"]}:{work}'}


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
    except json.JSONDecodeError:
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
    # A K91 experiment reference is optional; experiment_grade retains or refuses it.
    if (not isinstance(result, dict) or
            set(result) - {'experiment'} != RESULT_KEYS | ({'pairs'} if paired else set())):
        return 'contradiction', ['result.json shape differs from the pinned tool']
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
    if not isinstance(attempt, dict) or set(attempt) != ATTEMPT_KEYS | ({'pairs', 'pair_routing'} if paired else set()):
        return 'contradiction', ['attempt.json missing or shaped differently from the pinned tool']
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
    except (UnicodeDecodeError, json.JSONDecodeError):
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
    for a top-level input directory, reading literally <candidate root>/<name>.
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
            root, cand_dir.name, siblings, layout['candidate'])
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
    return problems, sha, report_state, inventory


def retain(run_dir, dest):
    """Copy the attempt's diagnostics, regular files only, no design files."""
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


def _identities(request, layout):
    def identity_of(root):
        return tree_identity(root) if root.is_dir() and not root.is_symlink() else None

    checkout, state = Path(layout['checkout']), Path(layout['state'])
    identities = {'code': {top: identity_of(checkout / top) for top in ('bin', 'src')},
                  'state': {comp: identity_of(state / comp) for comp in STATE_COMPONENTS[request['backend']]}}
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
    report['inputs'] = {'files': inputs, 'sha256': canonical_identity(inputs_tree)}
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
        problems += verify_state(checkout, state, request['backend'], layout['code'])
    for rel in STATE_REQUIRED[request['backend']]:
        if not os.path.lexists(state / rel):
            problems.append(f'missing installed backend dependency: KICAD_ROUTE_STATE/{rel}')
    targets = layout['probe'] if layout['probe'] is not None else (
        [state] + [state / c for c in STATE_COMPONENTS[request['backend']] if (state / c).is_dir()] +
        ([checkout] if checkout != state else []))
    report['state_write_probe'] = write_probe(targets)
    if not all(p['refused'] for p in report['state_write_probe']):
        problems.append('the tool state or checkout is writable from the sandbox')
    report['identity_before'] = _identities(request, layout)
    if problems:
        report['semantics'] = {'classification': 'not_run', 'reasons': problems, 'exit_code': None}
        experiment = experiment_grade(request, None, routing / 'attempt', None, routing)
        if experiment is not None:
            report['semantics']['experiment'] = experiment
        return _finish(report, routing)

    argv, env = tool_argv(request, layout), tool_env(layout)
    if 'experiment_policy' in request:
        # The router reads its routability-policy/1 file here; verify re-checks the bytes.
        (routing / 'attempt' / EXPERIMENT_POLICY_FILE).write_text(canonical_json(request['experiment_policy']))
    report['process'] = run_tool(argv, env, routing, routing / 'kicad-route.stdout', routing / 'kicad-route.stderr',
                                 request['timeout_s'], cancel)
    report['identity_after'] = _identities(request, layout)
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
    sidecar = retain_experiment(result, run_dir, routing)
    if sidecar is not None:
        report['retained'][f'attempt/{sidecar}'] = digest(routing / 'attempt' / sidecar)
    experiment = experiment_grade(request, result, routing / 'attempt', run_dir, routing)
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
    if report['identity_after'] != report['identity_before']:
        violations.append('tool code or state changed during the attempt')
    if after_inputs != inputs_tree:
        violations.append('candidate inputs changed during the attempt')
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
