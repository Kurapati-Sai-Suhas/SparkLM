import os
import sys
import django

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'LearnLM.settings')
django.setup()

from groups.coding_views import _run_on_judge0
from groups.models import Question

q = Question.objects.filter(title__icontains='Two Sum III').first()

java_code = """
import java.util.*;

class Solution {
    public Object twoSumIiiDataStructureDesign(Object... args) {
        String input = (String) args[0];
        String[] lines = input.split("\\n");
        if (lines.length < 2) return "None";
        
        String[] numStrs = lines[0].trim().split("\\\\s+");
        int target = Integer.parseInt(lines[1].trim());
        
        Map<Integer, Integer> map = new HashMap<>();
        for (int i = 0; i < numStrs.length; i++) {
            if (numStrs[i].isEmpty()) continue;
            int num = Integer.parseInt(numStrs[i]);
            int comp = target - num;
            if (map.containsKey(comp)) {
                return map.get(comp) + " " + i;
            }
            map.put(num, i);
        }
        return "None";
    }
}
"""

wrapper_template = q.hidden_wrapper_code.get('java') if q.hidden_wrapper_code else None

if not wrapper_template:
    wrapper_template = """import java.util.*;
import java.lang.reflect.*;

public class Main {
    public static void main(String[] args) {
        Scanner scanner = new Scanner(System.in);
        StringBuilder sb = new StringBuilder();
        while (scanner.hasNextLine()) {
            sb.append(scanner.nextLine()).append("\\n");
        }
        String input = sb.toString().trim();
        
        try {
            Solution sol = new Solution();
            Method[] methods = Solution.class.getDeclaredMethods();
            Method targetMethod = null;
            for (Method m : methods) {
                String name = m.getName();
                int modifiers = m.getModifiers();
                if (Modifier.isPublic(modifiers) && !name.equals("<init>") && !name.contains("main") && !name.contains("$")) {
                    targetMethod = m;
                    break;
                }
            }
            
            if (targetMethod == null) return;
            
            Class<?>[] paramTypes = targetMethod.getParameterTypes();
            Object[] argsToPass = new Object[paramTypes.length];
            
            if (paramTypes.length > 0) {
                Class<?> pType = paramTypes[0];
                if (pType == int.class || pType == Integer.class) {
                    argsToPass[0] = Integer.parseInt(input);
                } else if (pType == double.class || pType == Double.class) {
                    argsToPass[0] = Double.parseDouble(input);
                } else {
                    argsToPass[0] = input;
                }
            }
            
            Object result;
            if (targetMethod.isVarArgs()) {
                result = targetMethod.invoke(sol, new Object[]{argsToPass});
            } else {
                result = targetMethod.invoke(sol, argsToPass);
            }
            if (result != null) {
                System.out.println(result.toString().replace(" ", ""));
            }
        } catch (Exception e) {
            e.printStackTrace();
        }
    }
}
{user_code}"""

executable = wrapper_template.replace("{user_code}", java_code)
print(_run_on_judge0(executable, 'java', '1 2 3 4 5\n7'))
print(_run_on_judge0(executable, 'java', '5 5 11 5\n10'))
print(_run_on_judge0(executable, 'java', ''))
print(_run_on_judge0(executable, 'java', '1\n2'))
