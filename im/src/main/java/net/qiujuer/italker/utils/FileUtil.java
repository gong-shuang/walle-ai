package net.qiujuer.italker.utils;

import java.io.File;

public class FileUtil {

    /**
     * 删除某个文件夹下的所有文件夹和文件
     * @param file
     * @return
     */
    public static boolean deletefile(File file){
        if(!file.isDirectory()){
            if(!file.delete()){
                return false;
            }
        }else{
            File[] files = file.listFiles();
            for(File fileItem : files){
                if(fileItem.isDirectory()){
                    if(!deletefile(fileItem)){
                        return false;
                    }
                }else {
                    if(!fileItem.delete()){
                        return false;
                    }
                }
            }
            if(!file.delete()){
                return false;
            }
        }
        return true;
    }
}
