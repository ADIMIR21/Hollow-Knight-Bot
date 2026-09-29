// Look for stubs (NotImplementedException / PlatformNotSupportedException) in the
// System.IO.Pipes types of the given assembly. IL is read directly through
// System.Reflection.Metadata — nothing has to be executed.
using System.Collections.Immutable;
using System.Reflection.Metadata;
using System.Reflection.Metadata.Ecma335;
using System.Reflection.PortableExecutable;

string path = args.Length > 0 ? args[0] : @"System.Core.dll";
// Without a type list we print the whole System.IO.Pipes namespace; otherwise only
// the named types.
string[] wantedTypes = args.Length > 1 ? args[1..] : Array.Empty<string>();

using var fs = File.OpenRead(path);
using var pe = new PEReader(fs);
MetadataReader md = pe.GetMetadataReader();

Console.WriteLine($"File: {path}");
Console.WriteLine($"Types in assembly: {md.TypeDefinitions.Count}");
// A discovery mode: print every type whose full name contains the given substring. The type probe
// below can only look for names it is told, and the game's own classes are in the global namespace
// with names nobody can guess, so this is how they are found in the first place.
if (args.Length > 1 && args[1] == "--list")
{
    string needle = args.Length > 2 ? args[2] : "";
    foreach (TypeDefinitionHandle h in md.TypeDefinitions)
    {
        TypeDefinition t = md.GetTypeDefinition(h);
        string nsName = md.GetString(t.Namespace);
        string full = nsName.Length == 0 ? md.GetString(t.Name) : nsName + "." + md.GetString(t.Name);
        if (full.Contains(needle, StringComparison.OrdinalIgnoreCase))
            Console.WriteLine("  " + full);
    }
    return;
}

foreach (TypeDefinitionHandle tdh in md.TypeDefinitions)
{
    TypeDefinition td = md.GetTypeDefinition(tdh);
    string ns = md.GetString(td.Namespace);
    string name = md.GetString(td.Name);
    if (wantedTypes.Length == 0 && ns != "System.IO.Pipes") continue;
    if (wantedTypes.Length > 0 && !wantedTypes.Contains(name)) continue;

    Console.WriteLine();
    Console.WriteLine($"=== {ns}.{name} ===");
    foreach (FieldDefinitionHandle fh in td.GetFields())
    {
        FieldDefinition f = md.GetFieldDefinition(fh);
        string ftype;
        try { ftype = f.DecodeSignature(new TypeNameProvider(), null); }
        catch { ftype = "?"; }
        Console.WriteLine($"  field {ftype} {md.GetString(f.Name)}");
    }    var provider = new TypeNameProvider();
    foreach (MethodDefinitionHandle mh in td.GetMethods())
    {
        MethodDefinition m = md.GetMethodDefinition(mh);
        string mname = md.GetString(m.Name);
        string pars;
        try
        {
            MethodSignature<string> sig = m.DecodeSignature(provider, null);
            pars = string.Join(", ", sig.ParameterTypes);
        }
        catch (Exception e) { pars = "? (" + e.GetType().Name + ")"; }

        Console.WriteLine($"  {Vis(m)} {mname}({pars})");
        Console.WriteLine($"      -> {StubKind(pe, md, m)}");
    }
}

static string Vis(MethodDefinition m)
{
    var a = m.Attributes;
    string access = (a & System.Reflection.MethodAttributes.Public) != 0 ? "public"
        : (a & System.Reflection.MethodAttributes.Assembly) != 0 ? "internal"
        : (a & System.Reflection.MethodAttributes.Family) != 0 ? "protected"
        : (a & System.Reflection.MethodAttributes.Private) != 0 ? "private"
        : "other";
    bool isStatic = (a & System.Reflection.MethodAttributes.Static) != 0;
    return access + (isStatic ? " static" : "");
}
static string StubKind(PEReader pe, MetadataReader md, MethodDefinition m)
{
    if (m.RelativeVirtualAddress == 0) return "no body (extern/abstract)";
    MethodBodyBlock body;
    try { body = pe.GetMethodBody(m.RelativeVirtualAddress); }
    catch (Exception e) { return "cannot read body: " + e.Message; }
    byte[] il = body.GetILBytes();
    if (il == null) return "no IL";

    var stubs = new List<string>();
    var calls = new List<string>();
    for (int i = 0; i < il.Length; i++)
    {
        // 0x73 newobj <token>
        if (il[i] == 0x73 && i + 4 < il.Length)
        {
            int token = BitConverter.ToInt32(il, i + 1);
            string tn = ResolveToken(md, token);
            if (tn.Contains("NotImplemented") || tn.Contains("PlatformNotSupported") || tn.Contains("NotSupported"))
                stubs.Add(tn);
        }
    }
    if (stubs.Count > 0) return "STUB: " + string.Join(" | ", stubs.Distinct());

    // Show calls to other methods of the same type (delegation chain)
    for (int i = 0; i < il.Length; i++)
    {
        if ((il[i] == 0x28 || il[i] == 0x6F) && i + 4 < il.Length)
        {
            string tn = ResolveToken(md, BitConverter.ToInt32(il, i + 1));
            if (tn.StartsWith("NamedPipe") || tn.StartsWith("PipeStream") || tn.StartsWith("Win32") || tn.StartsWith("Unix"))
                calls.Add(tn);
        }
    }
    string suffix = calls.Count > 0 ? " | calls: " + string.Join(", ", calls.Distinct()) : "";
    return $"IL {il.Length} bytes, locals {body.LocalSignature.IsNil switch { true => 0, false => 1 }}{suffix}";
}

static string ResolveToken(MetadataReader md, int token)
{
    try
    {
        EntityHandle handle = MetadataTokens.EntityHandle(token);
        switch (handle.Kind)
        {
            case HandleKind.MemberReference:
                MemberReference mr = md.GetMemberReference((MemberReferenceHandle)handle);
                string pars = "";
                try
                {
                    var prov = new TypeNameProvider();
                    if (mr.GetKind() == MemberReferenceKind.Method)
                        pars = string.Join(", ", mr.DecodeMethodSignature(prov, null).ParameterTypes);
                    else
                        pars = mr.DecodeFieldSignature(prov, null);
                }
                catch { pars = "?"; }
                return TypeOfParent(md, mr.Parent) + "." + md.GetString(mr.Name) + "(" + pars + ")";
            case HandleKind.MethodDefinition:
                MethodDefinition m2 = md.GetMethodDefinition((MethodDefinitionHandle)handle);
                string pars2 = "";
                try { pars2 = string.Join(", ", m2.DecodeSignature(new TypeNameProvider(), null).ParameterTypes); }
                catch { pars2 = "?"; }
                return md.GetString(md.GetTypeDefinition(m2.GetDeclaringType()).Name) + "." + md.GetString(m2.Name) + "(" + pars2 + ")";
            case HandleKind.TypeReference:
                return md.GetString(md.GetTypeReference((TypeReferenceHandle)handle).Name);
            default:
                return handle.Kind.ToString();
        }
    }
    catch { return "token:" + token; }
}

static string TypeOfParent(MetadataReader md, EntityHandle parent)
{
    if (parent.Kind == HandleKind.TypeReference)
        return md.GetString(md.GetTypeReference((TypeReferenceHandle)parent).Name);
    if (parent.Kind == HandleKind.TypeDefinition)
        return md.GetString(md.GetTypeDefinition((TypeDefinitionHandle)parent).Name);
    return "?";
}

sealed class TypeNameProvider : ISignatureTypeProvider<string, object>
{
    public string GetPrimitiveType(PrimitiveTypeCode typeCode) => typeCode.ToString();
    public string GetTypeFromDefinition(MetadataReader reader, TypeDefinitionHandle handle, byte rawTypeKind)
        => reader.GetString(reader.GetTypeDefinition(handle).Name);
    public string GetTypeFromReference(MetadataReader reader, TypeReferenceHandle handle, byte rawTypeKind)
        => reader.GetString(reader.GetTypeReference(handle).Name);
    public string GetTypeFromSpecification(MetadataReader reader, object genericContext, TypeSpecificationHandle handle, byte rawTypeKind)
        => "spec";
    public string GetSZArrayType(string elementType) => elementType + "[]";
    public string GetArrayType(string elementType, ArrayShape shape) => elementType + "[]";
    public string GetByReferenceType(string elementType) => "ref " + elementType;
    public string GetPointerType(string elementType) => elementType + "*";
    public string GetGenericInstantiation(string genericType, ImmutableArray<string> typeArguments)
        => genericType + "<" + string.Join(",", typeArguments) + ">";
    public string GetGenericMethodParameter(object genericContext, int index) => "!!" + index;
    public string GetGenericTypeParameter(object genericContext, int index) => "!" + index;
    public string GetModifiedType(string modifier, string unmodifiedType, bool isRequired) => unmodifiedType;
    public string GetPinnedType(string elementType) => elementType;
    public string GetFunctionPointerType(MethodSignature<string> signature) => "fnptr";
}
