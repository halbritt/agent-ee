"""Campaign acquisition recipe implementations."""
from . import alternative
from . import alternative as acquisition_alternative
from . import alternative_context
from . import alternative_context as acquisition_alternative_context
from . import block_source
from . import block_source as acquisition_block_source
from . import completion
from . import completion as acquisition_completion
from . import connector_geometry
from . import connector_geometry as acquisition_connector_geometry
from . import expected_intent
from . import expected_intent as acquisition_expected_intent
from . import film
from . import film as acquisition_film
from . import film_analysis
from . import film_analysis as acquisition_film_analysis
from . import film_removal
from . import film_removal as acquisition_film_removal
from . import ground_ties
from . import ground_ties as acquisition_ground_ties
from . import local_geometry
from . import local_geometry as acquisition_local_geometry
from . import local_return
from . import local_return as acquisition_local_return
from . import local_routing
from . import local_routing as acquisition_local_routing
from . import mechanics
from . import mechanics as acquisition_mechanics
from . import placement
from . import placement as acquisition_placement
from . import power_analysis
from . import power_analysis as acquisition_power_analysis
from . import power_geometry
from . import power_geometry as acquisition_power_geometry
from . import power_parts
from . import power_parts as acquisition_power_parts
from . import power_prepare
from . import power_prepare as acquisition_power_prepare
from . import power_repair
from . import power_repair as acquisition_power_repair
from . import prepare
from . import prepare as acquisition_prepare
from . import resistor
from . import resistor as acquisition_resistor
from . import reverse_blocking
from . import reverse_blocking as acquisition_reverse_blocking
from . import roles
from . import roles as acquisition_roles
from . import selected_cpl
from . import selected_cpl as acquisition_selected_cpl
from . import source_supplement
from . import source_supplement as acquisition_source_supplement
from . import addr_repair
from . import bundle_helpers

import sys
for _name, _mod in list(locals().items()):
    if not _name.startswith('_'):
        sys.modules[f'agent_ee.acquisition.{_name}'] = _mod
