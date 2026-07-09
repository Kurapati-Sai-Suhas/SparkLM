import os
import sys
import django

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'LearnLM.settings')
django.setup()

from groups.coding_views import _run_on_judge0
from groups.models import Question

q = Question.objects.filter(title__icontains='Missing Ranges').first()

java_code = """
class Solution {
    public Object missingRanges(Object... args) {
        String input = (String) args[0];
        String[] lines = input.split("\\n");
        if (lines.length < 1) return "";
        
        String[] bounds = lines[0].trim().split("\\\\s+");
        if (bounds.length < 2) return "";
        int lower = Integer.parseInt(bounds[0]);
        int upper = Integer.parseInt(bounds[1]);
        
        int[] nums = new int[0];
        if (lines.length > 1 && !lines[1].trim().isEmpty()) {
            String[] numStrs = lines[1].trim().split("\\\\s+");
            nums = new int[numStrs.length];
            for (int i = 0; i < numStrs.length; i++) {
                nums[i] = Integer.parseInt(numStrs[i]);
            }
        }
        
        java.util.List<String> res = new java.util.ArrayList<>();
        int next = lower;
        for (int i = 0; i < nums.length; i++) {
            if (nums[i] < next) continue;
            if (nums[i] == next) {
                next++;
                continue;
            }
            res.add(formatRange(next, nums[i] - 1));
            next = nums[i] + 1;
        }
        if (next <= upper) {
            res.add(formatRange(next, upper));
        }
        return String.join(", ", res);
    }
    
    private String formatRange(int lower, int upper) {
        if (lower == upper) {
            return String.valueOf(lower);
        }
        return lower + "-" + upper;
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
                if (!name.equals("<init>") && !name.contains("main") && !name.contains("$")) {
                    targetMethod = m;
                    break;
                }
            }
            
            Object[] argsToPass = new Object[]{input};
            Object result = targetMethod.invoke(sol, new Object[]{argsToPass});
            if (result != null) {
                System.out.println(result.toString().replace(" ", ""));
            }
        } catch (Exception e) {
            e.printStackTrace();
        }
    }
}
{user_code}
"""

executable = wrapper_template.replace("{user_code}", java_code)
result = _run_on_judge0(executable, 'java', '0 99\n0 1 3 50 75')
print("Test 1 Result:", result)

result2 = _run_on_judge0(executable, 'java', '0 10\n1 2 3 4 5 6 7 8 9 10')
print("Test 2 Result:", result2)
