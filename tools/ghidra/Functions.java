// List every function whose entry point falls in [lo, hi), with size and callers.
//   tools/ghidra.sh run Functions.java <outfile> <lohex> <hihex>
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionIterator;
import ghidra.program.model.symbol.Reference;
import java.io.PrintWriter;

public class Functions extends GhidraScript {
    public void run() throws Exception {
        String[] args = getScriptArgs();
        PrintWriter w = new PrintWriter(args[0]);
        long lo = Long.decode(args[1]), hi = Long.decode(args[2]);
        FunctionIterator it = currentProgram.getFunctionManager().getFunctions(true);
        int n = 0;
        while (it.hasNext()) {
            Function f = it.next();
            long e = f.getEntryPoint().getOffset();
            if (e < lo || e >= hi) continue;
            int refs = 0;
            for (Reference r : currentProgram.getReferenceManager()
                    .getReferencesTo(f.getEntryPoint())) refs++;
            w.println(String.format("%08x  size=%-6d refs=%-4d %s", e,
                f.getBody().getNumAddresses(), refs, f.getName()));
            n++;
        }
        w.println("# " + n + " functions in range");
        w.close();
        println("wrote " + args[0] + " (" + n + " functions)");
    }
}
