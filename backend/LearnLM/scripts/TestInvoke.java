import java.lang.reflect.*;

public class TestInvoke {
    public static void main(String[] args) {
        try {
            Main sol = new Main();
            Method targetMethod = Main.class.getDeclaredMethod("myMethod", Object[].class);
            
            Class<?>[] paramTypes = targetMethod.getParameterTypes();
            Object[] argsToPass = new Object[paramTypes.length];
            argsToPass[0] = "Hello World";
            
            System.out.println("Invoking...");
            Object result = targetMethod.invoke(sol, argsToPass);
            System.out.println("Success! Result: " + result);
        } catch (Exception e) {
            e.printStackTrace();
        }
    }
    
    public Object myMethod(Object... args) {
        System.out.println("myMethod called! args length: " + args.length);
        System.out.println("args[0]: " + args[0]);
        return "Done";
    }
}
