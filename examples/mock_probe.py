from aiq_lean_tools.census import load_census
from aiq_lean_tools.lean_backend import MockLeanBackend

census = load_census("examples/minimal-census.json", root=".")
backend = MockLeanBackend({
    "ExamplePaper.theorem_one": "ExamplePaper.theorem_one : True",
})
probe = census.probe(
    backend=backend,
    imports=["ExamplePaper"],
)
print(probe.to_json())
