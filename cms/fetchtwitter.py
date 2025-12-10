from requests_oauthlib import OAuth1Session
import json
import pprint
import os
import time





class FetchTwiter():
    
    def __init__(self , api_key , api_secret_key , access_token , access_token_secret) :
        

        #header = {"Authorization" : f"Bearer {bearer}"}
        self.twitter = OAuth1Session(api_key , api_secret_key , access_token , access_token_secret)

        self.url = "https://api.x.com/2/users/"


    def get_Id(self):
        print(os.getcwd())
        
        res = self.twitter.get(f"{self.url}me")

        data = res.json()
        print(data)
        id = data["data"]["id"]
        with open("./social/twitter_id.txt" , "+w") as i:
            i.write(id)
            
            
        return id
        
    def setLastFetchedTime(self):
        with open("social/time.txt" , "w") as f:
            f.write(str(time.time()))
    
    def getTweets(self):  
     
        try:
            """ open the last time a request was made"""
            with open("social/time.txt", "r") as f:
              prev_time =  float(f.readline())  
        except:
            """ if the file is missing save a new time """
            self.setLastFetchedTime()
            prev_time = time.time()
        
        finally:
            
            current_time = time.time()

            interval = 8 * 3600
            
        if current_time - prev_time >= interval :  
             try: 
                 """ Check if the file exists and read the id saved in it"""
                 with open("social/twitter_id.txt" , "+r") as i:
                     twitter_id = i.readline() 

             except : 
                 """ If the file doesn't exists run the the get id function"""
                 twitter_id = self.get_Id()

             finally:   
                 """ either the file exists before or a new run (due to the exception )

               fetch the tweeter post""" 
             params = {
                 "expansions": "attachments.media_keys",
                 "media.fields": "url,type,width,height,preview_image_url,variants",
                 "tweet.fields": "attachments"
               }

             tweets = self.twitter.get(f"{self.url}{twitter_id}/tweets",  params = params)

             json_res = tweets.json()
             error = json_res.get("errors",None)
             status = json_res.get("status" , 200)

             if error:
                 """ if an error exists, re run the getId function , it is likely the file is corrupted"""
                 # remove the file
                 os.unlink("social/twitter_id.txt")
                 # run the file again

                 print("i removed the id File is corrupted and i will return the old twitter post")
                 self.get_Id()
                 
                 # return the old file pending the time we can make a new request
                 try:
                     with open("social/twitter_post.json" , "r") as f:
                        data = json.load(f)
                 except:
                    return {"data":"Unable to fetch post"}

                 return data


             elif str(status).startswith("2"):
                 ''' if status begins with 2 i.e 200 
                 it a successful fetch'''

                 tweet = json.dumps(tweets.json() , indent=4)

                 """ Save the file for future use """
                 with open("social/twitter_post.json" ,"w") as f:
                     json.dump(json_res , f , indent=4)

                 pprint.pprint(tweet)
                 #self that last time fetched to the current time
                 self.setLastFetchedTime()
                 return tweet   

             else:
                 """ i.e status was unsuccessful or there was an error , 
                 return the old file"""

                 print("i ran the old file")
                 try:
                     with open("social/twitter_post.json" , "r") as f:
                        data = json.load(f)
                 except:
                    return {"data":"Unable to fetch post"}



                 return data
        
        else:
            """ interval not yet up to 8 hrs, i return the last saved file , 
                """

            print("interval not yet up to 8 hrs, i return the last saved file , ")
            try:
                with open("social/twitter_post.json" , "r") as f:
                        data = json.load(f)
            except:
                return {"data":"Unable to fetch post"}



            return data
                                                                                        