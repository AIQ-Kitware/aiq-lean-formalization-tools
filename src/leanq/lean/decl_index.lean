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

    lake env lean --run decl_index.lean <LibraryRoot> [<modulesFile>] [full|deps|graph|statement] [<namesFile>] [<boundaryPrefixes>]

`full` is the ordinary leanq index. It records proposition classification, source ranges and the
transitive axiom closure used for honest `sorryAx` reporting.

`deps` is a structural fast path for dependency-boundary queries such as `leanq promotions`. It
records only public declaration identity, kind and direct constant dependencies. Expensive metadata
is emitted as JSON `null` rather than fabricated.

`graph` is the dependency-complete structural mode used by `leanq graph`. Unlike inventory and
promotion queries, it retains internal/private constants and references so a public theorem's path
through private proof support is not cut out of the declaration graph. These support nodes are
marked with `internal: true` and can be hidden by presentation tooling after reachability is known.

Every mode records `typeDeps` -- the constants used by the declaration's *type* -- next to the
merged `deps` list, so a consumer can tell an edge that shapes a statement from one that only
supports its proof.

`statement` is the optional statement-closure sidecar. Starting from the names in `<namesFile>`
(or every declaration of the library when that file is absent) it walks the constants a statement
*means*: a definition is unfolded through the constants of its body, a structure or class through
the constants of its constructor fields, and a theorem is a leaf. Constants whose module begins
with one of the comma-separated `<boundaryPrefixes>` (default: the Lean core and Mathlib
ecosystem) are emitted as boundary leaves but never unfolded. Each record carries the
pretty-printed type, a structural hash of the elaborated type, the docstring, and for structures
the projection types of the fields, plus the `#check`-style signature, so a source-to-Lean audit can see every hypothesis a compact
predicate hides without trusting a hand-written dictionary.

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
      | '"' => "\\\"" | '\\' => "\\\\" | '\n' => "\\n" | '\t' => "\\t" | '\r' => "\\r"
      | c => c.toString)

def jsonStr (s : String) : String := "\"" ++ esc s ++ "\""

def jsonNames (names : List Name) : String :=
  "[" ++ String.intercalate "," (names.map fun d => jsonStr d.toString) ++ "]"

/-- Drop ASCII whitespace. Written out rather than relying on toolchain-sensitive trim names. -/
def stripWs (s : String) : String :=
  s.foldl (init := "") fun acc c =>
    if c == ' ' || c == '\t' || c == '\r' then acc else acc.push c

/-- Does this declaration return `Prop` once its arguments are consumed? -/
def propValued (type : Expr) : MetaM Bool :=
  forallTelescopeReducing type fun _ body => do
    let body ← whnfR body
    return body.isProp || (body.isSort && body.sortLevel!.isZero)

/-- Constants used by the declaration's type, in first-use order, without `self`. -/
def typeConstants (ci : ConstantInfo) (self : Name) (includeInternal : Bool) : List Name :=
  ci.type.getUsedConstants.toList
    |>.filter (fun d => (includeInternal || !d.isInternal) && d != self) |>.eraseDups

/-- Constants used by the declaration's value (the proof of a theorem, the body of a
definition), without `self`.  The default `value?` deliberately hides theorem values; opt in
explicitly so this records the declarations actually used to establish a theorem. -/
def valueConstants (ci : ConstantInfo) (self : Name) (includeInternal : Bool) : List Name :=
  ((ci.value? (allowOpaque := true)).map Expr.getUsedConstants).getD #[] |>.toList
    |>.filter (fun d => (includeInternal || !d.isInternal) && d != self) |>.eraseDups

def emitConst (root modName n : Name) (ci : ConstantInfo)
    (depsOnly graphMode : Bool) : MetaM Unit := do
  let typeDeps := typeConstants ci n graphMode
  let deps := (typeDeps ++ valueConstants ci n graphMode).eraseDups
  let internalStr := if n.isInternal then "true" else "false"
  if depsOnly then
    IO.println <| "{"
      ++ "\"name\":" ++ jsonStr n.toString ++ ","
      ++ "\"module\":" ++ jsonStr modName.toString ++ ","
      ++ "\"kind\":\"" ++ kindOf ci ++ "\","
      ++ "\"library\":" ++ jsonStr root.toString ++ ","
      ++ "\"internal\":" ++ internalStr ++ ","
      ++ "\"isProp\":null,"
      ++ "\"propValued\":null,"
      ++ "\"sorried\":null,"
      ++ "\"line\":null,"
      ++ "\"axioms\":null,"
      ++ "\"typeDeps\":" ++ jsonNames typeDeps ++ ","
      ++ "\"deps\":" ++ jsonNames deps
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
    IO.println <| "{"
      ++ "\"name\":" ++ jsonStr n.toString ++ ","
      ++ "\"module\":" ++ jsonStr modName.toString ++ ","
      ++ "\"kind\":\"" ++ kindOf ci ++ "\","
      ++ "\"library\":" ++ jsonStr root.toString ++ ","
      ++ "\"internal\":" ++ internalStr ++ ","
      ++ "\"isProp\":" ++ (if isP then "true" else "false") ++ ","
      ++ "\"propValued\":" ++ (if pv then "true" else "false") ++ ","
      ++ "\"sorried\":" ++ (if ax.contains ``sorryAx then "true" else "false") ++ ","
      ++ "\"line\":" ++ toString line ++ ","
      ++ "\"axioms\":" ++ jsonNames ax.toList ++ ","
      ++ "\"typeDeps\":" ++ jsonNames typeDeps ++ ","
      ++ "\"deps\":" ++ jsonNames deps
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

/-! ### Statement closure sidecar -/

/-- Module that declares `n`, when the environment knows it. -/
def moduleOf (env : Environment) (n : Name) : Name :=
  match env.getModuleIdxFor? n with
  | some idx => env.header.moduleNames[idx.toNat]!
  | none => Name.anonymous

/-- The library label for a module: the requested root when it owns the module, otherwise the
module's first component (`Mathlib.X.Y` → `Mathlib`). -/
def libraryOf (root modName : Name) : Name :=
  if root.isPrefixOf modName then root
  else match modName.components with
    | c :: _ => c
    | [] => Name.anonymous

def isBoundary (boundaryPrefixes : List Name) (modName : Name) : Bool :=
  boundaryPrefixes.any fun p => p.isPrefixOf modName

/-- Constants that the *meaning* of a declaration depends on beyond its type: the body of a
definition, the constructor fields of an inductive type.  Theorems, axioms and the rest are
leaves. -/
def bodyConstants (env : Environment) (ci : ConstantInfo) (self : Name) : List Name :=
  match ci with
  | .defnInfo _ => valueConstants ci self false
  | .inductInfo iv =>
      (iv.ctors.flatMap fun c =>
        match env.find? c with
        | some cci => typeConstants cci self false
        | none => [])
      |>.filter (fun d => d != self && !iv.ctors.contains d) |>.eraseDups
  | _ => []

def ppType (e : Expr) : MetaM String := do
  try
    let fmt ← ppExpr e
    pure (fmt.pretty (width := 100))
  catch _ => pure ""

def fieldsJson (env : Environment) (n : Name) : MetaM String := do
  match getStructureInfo? env n with
  | none => pure "null"
  | some info =>
      let mut parts : List String := []
      for field in info.fieldNames do
        let projName := match getProjFnForField? env n field with
          | some p => p
          | none => Name.anonymous
        let typeStr ← match env.find? projName with
          | some pci => ppType pci.type
          | none => pure ""
        parts := parts ++ ["{\"name\":" ++ jsonStr field.toString ++ ",\"projection\":"
          ++ jsonStr projName.toString ++ ",\"type\":" ++ jsonStr typeStr ++ "}"]
      pure ("[" ++ String.intercalate "," parts ++ "]")

def emitStatement (root : Name) (n : Name) (ci : ConstantInfo) (role : String)
    (boundaryPrefixes : List Name) : MetaM Unit := do
  let env ← getEnv
  let modName := moduleOf env n
  let typeDeps := typeConstants ci n false
  let bodyDeps := bodyConstants env ci n
  let typeStr ← ppType ci.type
  let sigStr ← try
      let fmt ← PrettyPrinter.ppSignature n
      pure (fmt.1.pretty (width := 100))
    catch _ => pure ""
  let doc ← try findDocString? env n catch _ => pure none
  let docStr := match doc with
    | some d => jsonStr d
    | none => "null"
  let fields ← fieldsJson env n
  let line ← try
      match ← findDeclarationRanges? n with
      | some r => pure r.range.pos.line
      | none => pure 0
    catch _ => pure 0
  let isP ← try isProp ci.type catch _ => pure false
  -- Flags let a renderer collapse the instance/projection plumbing that every
  -- Mathlib-typed statement drags in, and keep the classes and definitions that
  -- carry mathematical content in view.
  let inst ← try isInstance n catch _ => pure false
  let flags : List String :=
    (if inst then ["instance"] else [])
    ++ (if env.isProjectionFn n then ["projection"] else [])
    ++ (if isClass env n then ["class"] else [])
    ++ (if isStructure env n then ["structure"] else [])
  IO.println <| "{"
    ++ "\"name\":" ++ jsonStr n.toString ++ ","
    ++ "\"module\":" ++ jsonStr modName.toString ++ ","
    ++ "\"kind\":\"" ++ kindOf ci ++ "\","
    ++ "\"library\":" ++ jsonStr (libraryOf root modName).toString ++ ","
    ++ "\"role\":" ++ jsonStr role ++ ","
    ++ "\"flags\":[" ++ String.intercalate "," (flags.map jsonStr) ++ "],"
    ++ "\"boundary\":" ++ (if isBoundary boundaryPrefixes modName then "true" else "false") ++ ","
    ++ "\"isProp\":" ++ (if isP then "true" else "false") ++ ","
    ++ "\"line\":" ++ toString line ++ ","
    ++ "\"typeDeps\":" ++ jsonNames typeDeps ++ ","
    ++ "\"bodyDeps\":" ++ jsonNames bodyDeps ++ ","
    ++ "\"type\":" ++ jsonStr typeStr ++ ","
    ++ "\"signature\":" ++ jsonStr sigStr ++ ","
    ++ "\"typeExprHash\":" ++ jsonStr (toString ci.type.hash) ++ ","
    ++ "\"docstring\":" ++ docStr ++ ","
    ++ "\"fields\":" ++ fields
    ++ "}"

/-- Walk the statement closure of `seeds` and emit one record per reached constant.

A seed is emitted whether or not it lies inside a boundary prefix.  A constant reached
through the walk is emitted once; it is expanded only when its module is not a boundary
module.  `limit` guards against a runaway closure -- when it is hit the walk stops and a
warning goes to stderr, so the sidecar is visibly partial rather than silently so. -/
def emitStatements (root : Name) (seeds : List Name) (boundaryPrefixes : List Name)
    (limit : Nat := 20000) : MetaM Unit := do
  let env ← getEnv
  let mut seen : Std.HashSet Name := {}
  let mut queue : Array (Name × String) := #[]
  for s in seeds do
    unless seen.contains s do
      seen := seen.insert s
      queue := queue.push (s, "seed")
  let mut head := 0
  let mut emitted := 0
  while head < queue.size do
    let (n, role) := queue[head]!
    head := head + 1
    let some ci := env.find? n | do
      IO.println <| "{\"name\":" ++ jsonStr n.toString ++ ",\"role\":" ++ jsonStr role
        ++ ",\"missing\":true}"
      continue
    if emitted ≥ limit then
      IO.eprintln s!"leanq: statement closure hit the {limit}-constant limit; output is partial"
      break
    emitStatement root n ci role boundaryPrefixes
    emitted := emitted + 1
    let modName := moduleOf env n
    if role != "seed" && isBoundary boundaryPrefixes modName then continue
    let next := (typeConstants ci n false ++ bodyConstants env ci n).eraseDups
    for d in next do
      unless seen.contains d do
        seen := seen.insert d
        let dmod := moduleOf env d
        let drole := if isBoundary boundaryPrefixes dmod then "boundary" else "unfolded"
        queue := queue.push (d, drole)

/-- Every public declaration of the root library, as seeds for a whole-library sidecar. -/
def rootSeeds (root : Name) : MetaM (List Name) := do
  let env ← getEnv
  let mut out : Array Name := #[]
  for h : i in [0 : env.header.moduleNames.size] do
    let modName := env.header.moduleNames[i]
    unless root.isPrefixOf modName do continue
    for n in env.header.moduleData[i]!.constNames do
      if n.isInternal then continue
      out := out.push n
  pure out.toList

end DeclIndex

def defaultBoundaryPrefixes : List Name :=
  [`Init, `Lean, `Std, `Mathlib, `Batteries, `Aesop, `Qq, `ProofWidgets, `Plausible,
    `LeanSearchClient, `ImportGraph]

unsafe def main (args : List String) : IO Unit := do
  let root := (args.head?.getD "Mathlib").toName
  let detail := args.drop 2 |>.head?.getD "full"
  unless detail == "full" || detail == "deps" || detail == "graph" || detail == "statement" do
    throw <| IO.userError
      s!"unknown leanq index detail {detail}; expected full, deps, graph, or statement"
  let timings := (← IO.getEnv "LEANQ_TIMINGS") == some "1"
  initSearchPath (← findSysroot)
  let readNames (f : String) : IO (List Name) := do
    let txt ← IO.FS.readFile (System.FilePath.mk f)
    pure <| txt.splitOn "\n" |>.map DeclIndex.stripWs
      |>.filter (fun s => !s.isEmpty) |>.map String.toName
  let mods ← match args.drop 1 |>.head? with
    | none => pure #[root]
    | some f => do pure (← readNames f).toArray
  let seeds ← match args.drop 3 |>.head? with
    | none => pure none
    | some "" => pure none
    | some f => do pure (some (← readNames f))
  let boundary := match args.drop 4 |>.head? with
    | none => defaultBoundaryPrefixes
    | some "" => defaultBoundaryPrefixes
    | some s => s.splitOn "," |>.map DeclIndex.stripWs |>.filter (fun x => !x.isEmpty)
        |>.map String.toName

  -- Pretty-printing needs the imported notation unexpanders and delaborators, which live in
  -- environment extensions that `importModules` only evaluates when asked.  The structural
  -- modes never print a term, so they keep the cheaper import.
  let statementMode := detail == "statement"
  if statementMode then enableInitializersExecution
  let importStart ← IO.monoNanosNow
  let env ← importModules (mods.map fun m => { module := m }) {} (trustLevel := 1024)
    (loadExts := statementMode)
  let importStop ← IO.monoNanosNow
  if timings then
    IO.eprintln s!"leanq timing: import_ns={importStop - importStart} modules={mods.size}"

  -- unbounded: `whnf` on a few Mathlib-heavy types exceeds the default budget
  let opts : Options :=
    (({} : Options).setBool `pp.deepTerms true).set `pp.maxSteps (100000 : Nat)
  let ctx : Core.Context :=
    { fileName := "<decl-index>", fileMap := default, maxHeartbeats := 0, options := opts }
  let st : Core.State := { env := env }
  let emitStart ← IO.monoNanosNow
  if detail == "statement" then
    let action : MetaM Unit := do
      let seedList ← match seeds with
        | some s => pure s
        | none => DeclIndex.rootSeeds root
      DeclIndex.emitStatements root seedList boundary
    discard <| (action.run' {} {} |>.toIO ctx st)
  else
    discard <| ((DeclIndex.emit root detail).run' {} {} |>.toIO ctx st)
  let emitStop ← IO.monoNanosNow
  if timings then
    IO.eprintln s!"leanq timing: emit_ns={emitStop - emitStart} detail={detail}"
