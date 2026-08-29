/-
Copyright (c) 2026 Kitware, Inc. All rights reserved.
Released under Apache 2.0 license as described in the file LICENSE.
Authors: Jon Crall, Claude Opus 5
-/
import Lean

/-!
# Declaration index

Dumps one JSON object per declaration of a built Lean library, so semantic questions are answered
from the elaborated environment instead of by regexes over source text.

Run it against whichever project owns the modules:

    lake env lean --run decl_index.lean <LibraryRoot> [<modulesFile>] [full|deps|graph]

`full` is the ordinary leanq index. It records proposition classification, source ranges and the
transitive axiom closure used for honest `sorryAx` reporting.

`deps` is a structural fast path for dependency-boundary queries such as `leanq promotions`. It
records only public declaration identity, kind and direct constant dependencies. Expensive metadata
is emitted as JSON `null` rather than fabricated.

`graph` is the dependency-complete structural mode used by `leanq graph`. Unlike inventory and
promotion queries, it retains internal/private constants and references so a public theorem's path
through private proof support is not cut out of the declaration graph. These support nodes are
marked with `internal: true` and can be hidden by presentation tooling after reachability is known.

`<modulesFile>` holds one module name per line, and all of them are imported. Pass it for complete
whole-library inventory: a root module is not required to import every module Lake built.

Set `LEANQ_TIMINGS=1` for coarse Lean-side import-versus-index timings on stderr.
-/

open Lean Meta

namespace DeclIndex

def kindOf : ConstantInfo → String
  | .defnInfo _ => "def"
  | .thmInfo _ => "theorem"
  | .axiomInfo _ => "axiom"
  | .inductInfo _ => "inductive"
  | .ctorInfo _ => "ctor"
  | .opaqueInfo _ => "opaque"
  | .quotInfo _ => "quot"
  | .recInfo _ => "rec"

def esc (s : String) : String :=
  s.foldl (init := "") fun acc c =>
    acc ++ (match c with
      | '"' => "\\\"" | '\\' => "\\\\" | '\n' => "\\n" | '\t' => "\\t" | c => c.toString)

/-- Drop ASCII whitespace. Written out rather than relying on toolchain-sensitive trim names. -/
def stripWs (s : String) : String :=
  s.foldl (init := "") fun acc c =>
    if c == ' ' || c == '\t' || c == '\r' then acc else acc.push c

/-- Does this declaration return `Prop` once its arguments are consumed? -/
def propValued (type : Expr) : MetaM Bool :=
  forallTelescopeReducing type fun _ body => do
    let body ← whnfR body
    return body.isProp || (body.isSort && body.sortLevel!.isZero)

def dependencyJson (ci : ConstantInfo) (self : Name) (includeInternal : Bool) : String :=
  -- The proof of a theorem is opaque to reduction, but it is still present in
  -- the environment and is essential to a semantic *dependency* graph.  The
  -- default `value?` deliberately hides theorem values; opt in explicitly so
  -- this records the declarations actually used to establish a theorem.
  let used := ci.type.getUsedConstants ++
    (ci.value? (allowOpaque := true) |>.map Expr.getUsedConstants).getD #[]
  let deps := used.toList.filter (fun d => (includeInternal || !d.isInternal) && d != self) |>.eraseDups
  String.intercalate "," (deps.map fun d => "\"" ++ esc d.toString ++ "\"")

def emitConst (root modName n : Name) (ci : ConstantInfo)
    (depsOnly graphMode : Bool) : MetaM Unit := do
  let depStr := dependencyJson ci n graphMode
  let internalStr := if n.isInternal then "true" else "false"
  if depsOnly then
    IO.println <| "{"
      ++ "\"name\":\"" ++ esc n.toString ++ "\","
      ++ "\"module\":\"" ++ esc modName.toString ++ "\","
      ++ "\"kind\":\"" ++ kindOf ci ++ "\","
      ++ "\"library\":\"" ++ esc root.toString ++ "\","
      ++ "\"internal\":" ++ internalStr ++ ","
      ++ "\"isProp\":null,"
      ++ "\"propValued\":null,"
      ++ "\"sorried\":null,"
      ++ "\"line\":null,"
      ++ "\"axioms\":null,"
      ++ "\"deps\":[" ++ depStr ++ "]"
      ++ "}"
  else
    -- One pathological declaration must not abort the whole index.
    let ax ← try collectAxioms n catch _ => pure #[]
    let isP ← try isProp ci.type catch _ => pure false
    let pv ← try propValued ci.type catch _ => pure false
    let line ← try
        match ← findDeclarationRanges? n with
        | some r => pure r.range.pos.line
        | none => pure 0
      catch _ => pure 0
    let axStr := String.intercalate "," (ax.toList.map fun a => "\"" ++ esc a.toString ++ "\"")
    IO.println <| "{"
      ++ "\"name\":\"" ++ esc n.toString ++ "\","
      ++ "\"module\":\"" ++ esc modName.toString ++ "\","
      ++ "\"kind\":\"" ++ kindOf ci ++ "\","
      ++ "\"library\":\"" ++ esc root.toString ++ "\","
      ++ "\"internal\":" ++ internalStr ++ ","
      ++ "\"isProp\":" ++ (if isP then "true" else "false") ++ ","
      ++ "\"propValued\":" ++ (if pv then "true" else "false") ++ ","
      ++ "\"sorried\":" ++ (if ax.contains ``sorryAx then "true" else "false") ++ ","
      ++ "\"line\":" ++ toString line ++ ","
      ++ "\"axioms\":[" ++ axStr ++ "],"
      ++ "\"deps\":[" ++ depStr ++ "]"
      ++ "}"

/-- Emit declarations owned by `root` modules.

In graph mode, also recover private implementation constants directly from the
imported environment.  Lean private names are `_private.<module>...`; depending
on how a module was compiled they are not guaranteed to appear in the public
module header's `constNames`.  Missing one cuts an otherwise real dependency
path at the private helper, so the graph would falsely report that downstream
public theorems do not reach their upstream dependencies.
-/
def emit (root : Name) (detail : String) : MetaM Unit := do
  let env ← getEnv
  let graphMode := detail == "graph"
  let depsOnly := detail != "full"
  let mut seen : Array Name := #[]
  for h : i in [0 : env.header.moduleNames.size] do
    let modName := env.header.moduleNames[i]
    unless root.isPrefixOf modName do continue
    for n in env.header.moduleData[i]!.constNames do
      if n.isInternal && !graphMode then continue
      let some ci := env.find? n | continue
      emitConst root modName n ci depsOnly graphMode
      seen := seen.push n

  if graphMode then
    let privatePrefix := "_private." ++ root.toString ++ "."
    for (n, ci) in env.constants.toList do
      if n.isInternal && n.toString.startsWith privatePrefix && !(seen.contains n) then
        -- These rows have no reliable public module-header provenance.  The
        -- library root is sufficient for display; declaration identity and
        -- dependencies come from the environment constant itself.
        emitConst root root n ci depsOnly graphMode

end DeclIndex

unsafe def main (args : List String) : IO Unit := do
  let root := (args.head?.getD "Mathlib").toName
  let detail := args.drop 2 |>.head?.getD "full"
  unless detail == "full" || detail == "deps" || detail == "graph" do
    throw <| IO.userError s!"unknown leanq index detail {detail}; expected full, deps, or graph"
  let timings := (← IO.getEnv "LEANQ_TIMINGS") == some "1"
  initSearchPath (← findSysroot)
  let mods ← match args.drop 1 |>.head? with
    | none => pure #[root]
    | some f => do
        let txt ← IO.FS.readFile (System.FilePath.mk f)
        pure <| txt.splitOn "\n" |>.map DeclIndex.stripWs
          |>.filter (fun s => !s.isEmpty) |>.map String.toName |>.toArray

  let importStart ← IO.monoNanosNow
  let env ← importModules (mods.map fun m => { module := m }) {} (trustLevel := 1024)
  let importStop ← IO.monoNanosNow
  if timings then
    IO.eprintln s!"leanq timing: import_ns={importStop - importStart} modules={mods.size}"

  -- unbounded: `whnf` on a few Mathlib-heavy types exceeds the default budget
  let ctx : Core.Context :=
    { fileName := "<decl-index>", fileMap := default, maxHeartbeats := 0 }
  let st : Core.State := { env := env }
  let emitStart ← IO.monoNanosNow
  discard <| ((DeclIndex.emit root detail).run' {} {} |>.toIO ctx st)
  let emitStop ← IO.monoNanosNow
  if timings then
    IO.eprintln s!"leanq timing: emit_ns={emitStop - emitStart} detail={detail}"
