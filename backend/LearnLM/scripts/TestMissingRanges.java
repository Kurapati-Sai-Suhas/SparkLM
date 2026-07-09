import java.util.*;

public class TestMissingRanges {
    public static void main(String[] args) {
        String input = "0 99\n0 1 3 50 75";
        System.out.println("Output 1: " + solve(input));
        
        input = "0 10\n1 2 3 4 5 6 7 8 9 10";
        System.out.println("Output 2: " + solve(input));
    }
    
    public static Object solve(Object... args) {
        String input = (String) args[0];
        String[] lines = input.split("\n");
        if (lines.length < 1) return "";
        
        String[] bounds = lines[0].trim().split("\\s+");
        if (bounds.length < 2) return "";
        int lower = Integer.parseInt(bounds[0]);
        int upper = Integer.parseInt(bounds[1]);
        
        int[] nums = new int[0];
        if (lines.length > 1 && !lines[1].trim().isEmpty()) {
            String[] numStrs = lines[1].trim().split("\\s+");
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
    
    private static String formatRange(int lower, int upper) {
        if (lower == upper) {
            return String.valueOf(lower);
        }
        return lower + "-" + upper;
    }
}
